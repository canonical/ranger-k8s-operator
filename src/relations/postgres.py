# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines postgres relation event handling methods."""

import logging
from charms.data_platform_libs.v0.data_interfaces import DatabaseCreatedEvent
from ops import framework
from ops.model import WaitingStatus

from utils import log_event_handler

logger = logging.getLogger(__name__)


class PostgresRelationHandler(framework.Object):
    """Client for ranger:postgresql relations.

    Attributes:
        DB_NAME: the name of the postgresql database
    """

    DB_NAME = "ranger-k8s_db"

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "database")
        self.charm = charm

        # Handle database relation.
        charm.framework.observe(
            self.charm.postgres_relation.on.database_created,
            self._on_database_changed,
        )
        charm.framework.observe(
            self.charm.postgres_relation.on.endpoints_changed,
            self._on_database_changed,
        )
        charm.framework.observe(
            self.charm.on.database_relation_broken, self._on_database_relation_broken
        )

    @log_event_handler(logger)
    def _on_database_changed(self, event: DatabaseCreatedEvent) -> None:
        """Handle database creation/change events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.state.is_ready():
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        self.charm.unit.status = WaitingStatus(
            f"handling {event.relation.name} change"
        )
        self.update(event)

    @log_event_handler(logger)
    def _on_database_relation_broken(self, event: DatabaseCreatedEvent) -> None:
        """Handle broken relations with the database.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.state.is_ready():
            event.defer()
            return

        if self.charm.unit.is_leader():
            self.update(event, True)

    def update(self, event, relation_broken=False):
        """Assign nested value in peer relation.

        Args:
            event: The event triggered when the relation changed.
            relation_broken: true if database connection is broken.
        """
        db_conn = None
        if not relation_broken:
            host, port = event.endpoints.split(",", 1)[0].split(":")
            db_conn = {
                "dbname": PostgresRelationHandler.DB_NAME,
                "host": host,
                "port": port,
                "password": event.password,
                "user": event.username,
            }

        self.charm.state.database_connection = db_conn
        self.charm.update(event)

    def validate(self):
        """Check if the database connection is available.

        Raises:
            ValueError: if the database is not ready.
        """
        if self.charm.state.database_connection is None:
            raise ValueError("database relation not ready")
