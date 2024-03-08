# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines ldap relation event handling methods."""

import logging

from ops import framework

from literals import RELATION_VALUES
from utils import log_event_handler

logger = logging.getLogger(__name__)


class LDAPRelationHandler(framework.Object):
    """Client for ldap relations."""

    def __init__(self, charm, relation_name="ldap"):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
            relation_name: The name of the relation defaults to ldap.
        """
        super().__init__(charm, "ldap")
        self.charm = charm
        self.relation_name = relation_name

        # Handle database relation.
        self.framework.observe(
            charm.on[self.relation_name].relation_created,
            self._on_relation_created,
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

    @log_event_handler(logger)
    def _on_relation_created(self, event):
        """Handle ldap relation created.

        Args:
            event: The relation created event.
        """
        if not self.charm.unit.is_leader():
            return

        if self.charm.config["charm-function"].value != "usersync":
            return

        if event.relation:
            event.relation.data[self.charm.app].update({"user": "admin"})

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle ldap relation changed.

        Args:
            event: Relation changed event.
        """
        if not self.charm.unit.is_leader():
            return

        if self.charm.config["charm-function"].value != "usersync":
            return

        container = self.charm.model.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        event_data = event.relation.data[event.app]
        base_dn = event_data.get("base_dn")
        self.charm._state.ldap = {
            "sync_ldap_bind_password": event_data.get("admin_password"),
            "sync_ldap_bind_dn": f"cn=admin,{base_dn}",
            "sync_ldap_search_base": base_dn,
            "sync_ldap_user_search_base": base_dn,
            "sync_group_search_base": base_dn,
            "sync_ldap_url": event_data.get("ldap_url"),
        }

        self.charm.update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event):
        """Handle ldap relation broken.

        Args:
            event: Relation broken event.
        """
        if not self.charm.unit.is_leader():
            return

        if self.charm.config["charm-function"].value != "usersync":
            return

        container = self.charm.model.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        self.charm._state.ldap = {}
        self.charm.update(event)

    def validate(self):
        """Check if the required ldap parameters are available.

        Raises:
            ValueError: if ldap parameters are not available.
        """
        config = vars(self.charm.config)
        if not self.charm._state.ldap:
            for value in RELATION_VALUES:
                if not config.get(value):
                    raise ValueError(
                        "Add an LDAP relation or update config values."
                    )
