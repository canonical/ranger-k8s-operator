# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines trino-catalog relation event handling methods."""

import logging

from ops import framework

from utils import log_event_handler

logger = logging.getLogger(__name__)


class TrinoCatalogRelationHandler(framework.Object):
    """Client for trino-catalog relations."""

    def __init__(self, charm, relation_name="trino-catalog"):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
            relation_name: The name of the relation defaults to trino-catalog.
        """
        super().__init__(charm, "trino-catalog")
        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle trino-catalog relation changed.

        Args:
            event: Relation changed event.
        """
        if not self.charm.unit.is_leader():
            return

        container = self.charm.model.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        trino_info = self.charm.trino_catalog_requirer.get_trino_info()
        if trino_info:
            self.charm._state.trino_url = trino_info["trino_url"]
            self.charm._state.trino_catalogs = [
                c.to_dict() for c in trino_info["trino_catalogs"]
            ]
            self.charm._state.trino_credentials_secret_id = trino_info[
                "trino_credentials_secret_id"
            ]
        self.charm.update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event):
        """Handle trino-catalog relation broken.

        Args:
            event: Relation broken event.
        """
        if not self.charm.unit.is_leader():
            return

        container = self.charm.model.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        self.charm._state.trino_url = None
        self.charm._state.trino_catalogs = None
        self.charm._state.trino_credentials_secret_id = None
        self.charm.update(event)
