# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines PostgreSQL relation handling methods."""

import logging

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from ops import framework
from ops.model import ModelError, SecretNotFoundError

from exceptions import RelationNotReady

logger = logging.getLogger(__name__)


def _ca_certificates(tls, bundle):
    """Return the CA certificates to trust from PostgreSQL TLS relation data.

    Args:
        tls: The relation's `tls` value, or None when not advertised.
        bundle: The relation's `tls-ca` PEM bundle.

    Returns:
        The PEM-encoded CA certificates, or None when TLS is disabled.

    Raises:
        ValueError: If the TLS flag or CA bundle is invalid.
    """
    if tls is None or tls == "False":
        return None
    if tls != "True":
        raise ValueError("PostgreSQL relation has an invalid tls value; expected True or False")
    try:
        certificates = x509.load_pem_x509_certificates((bundle or "").encode())
    except ValueError as error:
        raise ValueError(
            "PostgreSQL relation enables TLS without a valid tls-ca PEM certificate bundle"
        ) from error
    ca_certificates = []
    for certificate in certificates:
        try:
            constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
        except x509.ExtensionNotFound:
            continue
        if constraints.value.ca:
            ca_certificates.append(certificate.public_bytes(Encoding.PEM).decode())
    if not ca_certificates:
        raise ValueError("PostgreSQL relation enables TLS without a CA certificate in tls-ca")
    return "".join(ca_certificates)


class PostgresRelationHandler(framework.Object):
    """Client for ranger:postgresql relations.

    Event observation is centralized in the charm; this object exposes logic methods
    invoked by the charm reconciler.

    Attributes:
        DB_NAME: the name of the postgresql database
    """

    DB_NAME = "ranger-k8s_db"

    def __init__(self, charm, relation_name="database"):
        """Construct.

        Args:
            charm: The charm to attach the handler to.
            relation_name: The name of the relation.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def get_connection(self):
        """Read PostgreSQL connection values live from the database relation.

        Returns:
            A database connection mapping, or None when unavailable. `tls_ca`
            holds the CA certificates to trust, or None for plaintext.

        Raises:
            ValueError: If the relation advertises invalid TLS data.
        """
        for relation in self.charm.model.relations[self.relation_name]:
            if not relation.active:
                continue
            try:
                data = self.charm.postgres_relation.fetch_relation_data(
                    [relation.id], ["endpoints", "username", "password", "tls", "tls-ca"]
                ).get(relation.id, {})
                host, port = data["endpoints"].split(",", 1)[0].split(":")
                connection = {
                    "dbname": self.DB_NAME,
                    "host": host,
                    "port": port,
                    "user": data["username"],
                    "password": data["password"],
                }
            except (KeyError, ModelError, SecretNotFoundError, ValueError) as error:
                logger.warning("Could not read database relation data: %s", error)
                continue
            connection["tls_ca"] = _ca_certificates(data.get("tls"), data.get("tls-ca"))
            return connection
        return None

    def validate(self):
        """Raise when the database relation is absent or not yet usable.

        Raises:
            ValueError: when no database relation exists or its TLS data is invalid.
            RelationNotReady: when the relation exists but has published no data.
        """
        if not self.charm.model.relations[self.relation_name]:
            raise ValueError("integrate ranger-k8s with a PostgreSQL database")
        if self.get_connection() is None:
            raise RelationNotReady("waiting for database")
