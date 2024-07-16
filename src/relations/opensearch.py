# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines postgres relation event handling methods."""

import logging

from ops import framework
from ops.model import WaitingStatus

from literals import JAVA_ENV
from utils import log_event_handler

logger = logging.getLogger(__name__)


class OpensearchRelationHandler(framework.Object):
    """Client for ranger:postgresql relations.

    Attributes:
        INDEX_NAME: the opensearch index name.
        CERTIFICATE_NAME: the name of the opensearch certificate
    """

    INDEX_NAME = "ranger_audits"
    CERTIFICATE_NAME = "opensearch-ca"

    def __init__(self, charm, relation_name="opensearch"):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        self.relation_name = relation_name
        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

        self.charm = charm

    @log_event_handler(logger)
    def _on_relation_changed(self, event) -> None:
        """Handle openserach relation changed events.

        Args:
            event: The event triggered when the relation changed.
        """
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
        if self.charm.unit.is_leader():
            self.update(event, True)

    def handle_certificates(self, event, relation_broken=False) -> None:
        """Adds the Opensearch certificate to the Java truststore.

        Args:
            event: The event triggered when the relation changed.
        """
        container = self.charm.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        if not relation_broken:
            command = [
                "keytool",
                "-importcert",
                "-keystore",
                "$JAVA_HOME/lib/security/cacerts",
                "-file",
                "opensearch.crt",
                "-alias",
                self.CERTIFICATE_NAME,
                "-storepass",
                "changeit",
            ]
        else:
            command = [
                "keytool",
                "-delete",
                "-keystore",
                "$JAVA_HOME/lib/security/cacerts",
                "-alias",
                self.CERTIFICATE_NAME,
                "-storepass",
                "changeit",
            ]
        container.exec(
            command,
            environment=JAVA_ENV,
        ).wait()

    def update(self, event, relation_broken=False):
        """Assign nested value in peer relation.

        Args:
            event: The event triggered when the relation changed.
            relation_broken: true if opensearch connection is broken.
        """
        env = {"is_enabled": False}
        self.handle_certificates(event, relation_broken)
        if not relation_broken:
            event_data = event.relation.data[event.app]
            host, port = (
                event_data.get("endpoints").split(",", 1)[0].split(":")
            )
            env = {
                "index": OpensearchRelationHandler.INDEX_NAME,
                "host": host,
                "port": port,
                "password": event_data.get("password"),
                "user": event_data.get("username"),
                "is_enabled": True,
            }

        self.charm._state.opensearch = env
        self.charm.update(event)
