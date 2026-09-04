# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines PostgreSQL relation handling methods."""

import logging

from ops import framework
from ops.model import ModelError, SecretNotFoundError

from exceptions import RelationNotReady

logger = logging.getLogger(__name__)


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
            A database connection mapping, or None when unavailable.
        """
        for relation in self.charm.model.relations[self.relation_name]:
            if not relation.active:
                continue
            try:
                data = self.charm.postgres_relation.fetch_relation_data(
                    [relation.id], ["endpoints", "username", "password"]
                ).get(relation.id, {})
                host, port = data["endpoints"].split(",", 1)[0].split(":")
                return {
                    "dbname": self.DB_NAME,
                    "host": host,
                    "port": port,
                    "user": data["username"],
                    "password": data["password"],
                }
            except (KeyError, ModelError, SecretNotFoundError, ValueError) as error:
                logger.warning("Could not read database relation data: %s", error)
        return None

    def validate(self):
        """Raise when the database relation is absent or not yet usable.

        Raises:
            ValueError: when no database relation exists.
            RelationNotReady: when the relation exists but has published no data.
        """
        if not self.charm.model.relations[self.relation_name]:
            raise ValueError("integrate ranger-k8s with a PostgreSQL database")
        if self.get_connection() is None:
            raise RelationNotReady("waiting for database")
