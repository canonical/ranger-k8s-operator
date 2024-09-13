# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines opensearch relation event handling methods."""

import logging
import re

import requests
from charms.data_platform_libs.v0.data_interfaces import IndexCreatedEvent
from ops import framework
from ops.model import WaitingStatus
from ops.pebble import ExecError
from requests.auth import HTTPBasicAuth

from literals import CERTIFICATE_NAME, HEADERS, INDEX_NAME, OPENSEARCH_SCHEMA
from utils import log_event_handler

logger = logging.getLogger(__name__)


class OpensearchRelationHandler(framework.Object):
    """Client for ranger:postgresql relations."""

    def __init__(self, charm, relation_name="opensearch"):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
            relation_name: The name of the relation.
        """
        self.relation_name = relation_name
        self.charm = charm

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            self.charm.opensearch_relation.on.index_created,
            self._on_index_created,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_relation_broken,
        )

    @log_event_handler(logger)
    def _on_index_created(self, event: IndexCreatedEvent) -> None:
        """Handle opensearch relation changed events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm._state.is_ready():
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        self.charm.unit.status = WaitingStatus(
            f"handling {self.relation_name} change"
        )
        self.update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with opensearch.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm._state.is_ready():
            return

        if self.charm.unit.is_leader():
            self.update(event, True)

    def update_certificates(self, relation_broken=False) -> None:
        """Add/remove the Opensearch certificate in the Java truststore.

        Args:
            relation_broken: If the event is a relation broken event.
        """
        container = self.charm.unit.get_container(self.charm.name)
        if not container.can_connect():
            logger.debug(f"Unable to connect to {self.charm.name} container.")
            return

        certificate = self.charm._state.opensearch_certificate
        truststore_pwd = self.charm._state.truststore_pwd

        if not relation_broken and certificate:
            container.push("/opensearch.crt", certificate)
            command = [
                "keytool",
                "-importcert",
                "-keystore",
                "$JAVA_HOME/lib/security/cacerts",
                "-file",
                "/opensearch.crt",
                "-alias",
                CERTIFICATE_NAME,
                "-storepass",
                truststore_pwd,
                "--no-prompt",
            ]
        else:
            command = [
                "keytool",
                "-delete",
                "-keystore",
                "$JAVA_HOME/lib/security/cacerts",
                "-alias",
                CERTIFICATE_NAME,
                "-storepass",
                truststore_pwd,
            ]
        try:
            container.exec(command).wait()
        except ExecError as e:
            if e.stdout and "already exists" in e.stdout:
                return
            logger.error(e.stdout)

    def get_secret_content(self, secret_id) -> dict:
        """Get the content of a juju secret by id.

        Args:
            secret_id: The Juju secret ID.

        Returns:
            content: The content of the secret.
        """
        secret = self.model.get_secret(id=secret_id)
        content = secret.get_content(refresh=True)
        return content

    def get_cert_value(self, event) -> None:
        """Get certificate from opensearch secret.

        Args:
            event: The index created event.
        """
        event_data = event.relation.data[event.app]
        secret_id = event_data.get("secret-tls")
        content = self.get_secret_content(secret_id)
        tls_ca = content["tls-ca"]
        pattern = r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----"
        certificates_list = re.findall(pattern, tls_ca, re.DOTALL)
        self.charm._state.opensearch_certificate = certificates_list[1]

    def get_conn_values(self, event) -> dict:
        """Get the connection values from the relation to Opensearch.

        Args:
            event: The event triggered by index created or relation broken events.

        Returns:
            A dictionary of connection values.
        """
        event_data = event.relation.data[event.app]
        secret_id = event_data.get("secret-user")
        user_credentials = self.get_secret_content(secret_id)

        host, port = event_data.get("endpoints").split(",", 1)[0].split(":")
        return {
            "index": INDEX_NAME,
            "host": host,
            "port": port,
            "password": user_credentials["password"],
            "username": user_credentials["username"],
            "is_enabled": True,
        }

    def add_opensearch_schema(self, env) -> None:
        """Add the Ranger audit schema to Opensearch.

        Args:
            env: The Opensearch connection values.

        Raises:
            e: The error produced by an unsuccessful request.
        """
        url = f"https://{env['host']}:{env['port']}/{env['index']}/_mapping"
        try:
            requests.put(
                url,
                auth=HTTPBasicAuth(env["username"], env["password"]),
                headers=HEADERS,
                json=OPENSEARCH_SCHEMA,
                verify=False,
                timeout=60,
            )  # nosec
        except requests.exceptions.RequestException as e:
            logger.error(
                f"An exception has occurred while adding the audit schema: {e}"
            )
            raise

    def update(self, event, relation_broken=False) -> None:
        """Assign nested value in peer relation.

        Args:
            event: The event triggered when the relation changed.
            relation_broken: true if opensearch connection is broken.
        """
        env = {"is_enabled": False}
        if not relation_broken:
            env = self.get_conn_values(event)
            self.add_opensearch_schema(env)
            self.get_cert_value(event)
        self.update_certificates(relation_broken)
        self.charm._state.opensearch = env
        self.charm.update(event)
