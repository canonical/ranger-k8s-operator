# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines LDAP relation handling methods."""

from ops import framework


class LDAPRelationHandler(framework.Object):
    """Client for LDAP relations.

    Event observation is centralized in the charm; this object exposes logic methods
    invoked by the charm reconciler.
    """

    def __init__(self, charm, relation_name="ldap"):
        """Construct.

        Args:
            charm: The charm to attach the handler to.
            relation_name: The name of the relation.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def publish_bind_user(self):
        """Publish the LDAP bind user for usersync deployments.

        Returns:
            None.
        """
        if not self.charm.unit.is_leader():
            return
        if self.charm.config["charm-function"].value != "usersync":
            return
        for relation in self.charm.model.relations[self.relation_name]:
            if relation.active:
                relation.data[self.charm.app].update({"user": "admin"})

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
