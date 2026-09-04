# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines OpenSearch relation handling methods."""

import logging
import re

import requests
from ops import framework
from ops.model import ModelError, SecretNotFoundError
from ops.pebble import ExecError
from requests.auth import HTTPBasicAuth

from literals import CERTIFICATE_NAME, HEADERS, INDEX_NAME, OPENSEARCH_SCHEMA

logger = logging.getLogger(__name__)


class OpensearchRelationHandler(framework.Object):
    """Client for ranger:opensearch relations.

    Event observation is centralized in the charm; this object exposes logic methods
    invoked by the charm reconciler.
    """

    def __init__(self, charm, relation_name="opensearch"):
        """Construct.

        Args:
            charm: The charm to attach the handler to.
            relation_name: The name of the relation.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def _get_relation(self):
        """Return the OpenSearch relation, or None when absent.

        Returns:
            The OpenSearch relation or None.
        """
        relation = self.charm.model.get_relation(self.relation_name)
        if relation is None or relation.app is None:
            return None
        return relation

    def get_secret_content(self, secret_id) -> dict:
        """Get the content of a Juju secret by ID.

        Args:
            secret_id: The Juju secret ID.

        Returns:
            The secret content.
        """
        secret = self.model.get_secret(id=secret_id)
        return secret.get_content(refresh=True)

    def gather_certificate(self):
        """Read the OpenSearch CA certificate from the relation via the model.

        Returns:
            The CA certificate (PEM), or None when unavailable.
        """
        relation = self._get_relation()
        if relation is None:
            return None
        try:
            secret_id = relation.data[relation.app].get("secret-tls")
            if not secret_id:
                return None
            tls_ca = self.get_secret_content(secret_id)["tls-ca"]
        except (KeyError, ModelError, SecretNotFoundError, ValueError) as error:
            logger.warning("Could not read OpenSearch tls-ca secret: %s", error)
            return None
        certificates_list = re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", tls_ca, re.DOTALL
        )
        if len(certificates_list) < 2:
            logger.warning(
                "OpenSearch tls-ca bundle has %d certificate(s); expected at least 2",
                len(certificates_list),
            )
            return None
        return certificates_list[1]

    def gather_connection(self) -> dict:
        """Read OpenSearch connection values from the relation via the model.

        Returns:
            A dictionary of connection values, disabled when unavailable.
        """
        relation = self._get_relation()
        if relation is None:
            return {"is_enabled": False}
        try:
            event_data = relation.data[relation.app]
            secret_id = event_data.get("secret-user")
            endpoints = event_data.get("endpoints")
            if not secret_id or not endpoints:
                return {"is_enabled": False}
            user_credentials = self.get_secret_content(secret_id)
            host, port = endpoints.split(",", 1)[0].split(":")
            return {
                "index": INDEX_NAME,
                "host": host,
                "port": port,
                "password": user_credentials["password"],
                "username": user_credentials["username"],
                "is_enabled": True,
            }
        except (KeyError, ModelError, SecretNotFoundError, ValueError) as error:
            logger.warning("Could not read OpenSearch connection data: %s", error)
            return {"is_enabled": False}

    def reconcile_index_mapping(self, conn: dict) -> None:
        """Reconcile the Ranger audit schema in OpenSearch.

        Args:
            conn: The OpenSearch connection values.
        """
        if not self.charm.unit.is_leader() or not conn.get("is_enabled"):
            return
        url = f"https://{conn['host']}:{conn['port']}/{conn['index']}/_mapping"
        try:
            requests.put(
                url,
                auth=HTTPBasicAuth(conn["username"], conn["password"]),
                headers=HEADERS,
                json=OPENSEARCH_SCHEMA,
                verify=False,
                timeout=60,
            )  # nosec
        except requests.exceptions.RequestException as error:
            logger.error("An exception has occurred while adding the audit schema: %s", error)

    def reconcile_truststore_certificate(self, container, certificate, truststore_pwd) -> None:
        """Reconcile the OpenSearch certificate in the Java truststore.

        Args:
            container: The workload container.
            certificate: The desired OpenSearch CA certificate, or None.
            truststore_pwd: The Java truststore password.
        """
        if not container.can_connect():
            logger.debug("Unable to connect to %s container.", self.charm.name)
            return

        certificate_path = "/opensearch.crt"
        certificate_exists = container.exists(certificate_path)
        if certificate is not None:
            if certificate_exists and container.pull(certificate_path).read() == certificate:
                return
            container.push(certificate_path, certificate)
            command = [
                "keytool",
                "-importcert",
                "-cacerts",
                "-file",
                certificate_path,
                "-alias",
                CERTIFICATE_NAME,
                "-storepass",
                truststore_pwd,
                "--no-prompt",
            ]
        elif certificate_exists:
            command = [
                "keytool",
                "-delete",
                "-cacerts",
                "-alias",
                CERTIFICATE_NAME,
                "-storepass",
                truststore_pwd,
            ]
        else:
            return

        try:
            container.exec(command).wait()
        except ExecError as error:
            if error.stdout and "already exists" in error.stdout:
                return
            logger.error(error.stdout)
            return

        if certificate is None:
            container.remove_path(certificate_path)
