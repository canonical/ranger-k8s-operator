
import logging


from charms.data_platform_libs.v0.database_requires import (
    DatabaseEvent,
    DatabaseRequires,
)

from ops import framework
from ops.model import WaitingStatus
from log import log_event_handler

logger = logging.getLogger(__name__)


class PostgresRelationHandler(framework.Object):
    """Client for temporal:postgresql relations."""

    DB_NAME = "ranger-k8s_db"

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "db")
        self.charm = charm

        # Handle db:pgsql relation.
        charm.framework.observe(self.charm.postgres_relation.on.database_created, self._on_database_changed)
        charm.framework.observe(self.charm.postgres_relation.on.endpoints_changed, self._on_database_changed)
        charm.framework.observe(self.charm.postgres_relation.on.db_relation_broken, self._on_database_relation_broken)

    @log_event_handler(logger)
    def _on_database_changed(self, event: DatabaseEvent) -> None:
        """Handle database creation/change events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.state.is_ready():
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        self.charm.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        self.update(event)

    @log_event_handler(logger)
    def _on_database_relation_broken(self, event: DatabaseEvent) -> None:
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
            if self.charm.state.database_connections is None:
                self.charm.state.database_connections = {"db": None}

            host, port = event.endpoints.split(",", 1)[0].split(":")
            db_conn = {
                "dbname": PostgresRelationHandler.DB_NAME,
                "host": host,
                "port": port,
                "password": event.password,
                "user": event.username,
            }

        database_connections = self.charm.state.database_connections
        database_connections[event.relation.name] = db_conn
        self.charm.state.database_connections = database_connections

        self.charm.update(event)

    def validate(self):
        """
        Checks if the database connection is available
        Raises: ValueError if the database is not ready

        """
        if self.charm.state.database_connections is None:
            raise ValueError("database relation not ready")

        for rel_name, db_conn in self.charm.state.database_connections.items():
            if db_conn is None:
                raise ValueError(f"{rel_name}:pgsql relation: no database connection available")
