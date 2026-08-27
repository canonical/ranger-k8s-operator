# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines ldap relation event handling methods."""

import logging

from ops import framework

from utils import log_event_handler, validation_error_handler

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
    @validation_error_handler
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
    @validation_error_handler
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

        self.charm.update(event)

    @log_event_handler(logger)
    @validation_error_handler
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

        self.charm.update(event)

    def relation_values(self):
        """Return usersync values derived from the active LDAP relation.

        Returns:
            LDAP values derived from remote application data, or an empty mapping.
        """
        relation = self.charm.model.get_relation(self.relation_name)
        if not relation or not relation.active or not relation.app:
            return {}

        event_data = relation.data[relation.app]
        base_dn = event_data.get("base_dn")
        if not base_dn:
            return {}

        return {
            "sync_ldap_bind_password": event_data.get("admin_password"),
            "sync_ldap_bind_dn": f"cn=admin,{base_dn}",
            "sync_ldap_search_base": base_dn,
            "sync_ldap_user_search_base": base_dn,
            "sync_group_search_base": base_dn,
            "sync_ldap_url": event_data.get("ldap_url"),
        }

    def validate(self):
        """Check if the required ldap parameters are available.

        Raises:
            ValueError: if ldap parameters are not available.
        """
        if self.relation_values():
            return

        missing = []
        if not self.charm.ldap_credentials:
            missing.append("ldap-credentials")
        if not self.charm.config["sync-ldap-url"]:
            missing.append("sync-ldap-url")
        if not self.charm.config["sync-ldap-search-base"]:
            missing.append("sync-ldap-search-base")
        if missing:
            raise ValueError(f"Missing required LDAP configuration: {', '.join(missing)}.")
