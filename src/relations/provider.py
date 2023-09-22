# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger client relation hooks & helpers."""


import logging

from apache_ranger.client import ranger_client
from apache_ranger.model import ranger_service
from ops.charm import CharmBase
from ops.framework import Object

from utils import generate_password, log_event_handler

logger = logging.getLogger(__name__)
RANGER_URL = "http://localhost:6080"


class RangerProvider(Object):
    """Defines functionality for the 'provides' side of the 'ranger-client' relation.

    Hook events observed:
        - relation-created
        - relation-updated
        - relation-broken
    """

    def __init__(
        self, charm: CharmBase, relation_name: str = "policy"
    ) -> None:
        """Construct RangerProvider object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed,
        )

        self.charm = charm

    def _create_ranger_service(self, ranger, data, event):
        """Create Trino service in Ranger.

        Args:
            ranger: ranger client
            data: relation data
            event: relation event
        """
        retrieved_service = ranger.get_service(data["name"])
        if retrieved_service is not None:
            logging.info("service exists already")
            return

        service = ranger_service.RangerService(
            {"name": data["name"], "type": data["type"]}
        )
        password = generate_password(12)
        service.configs = {
            "username": f"relation_id_{event.relation.id}",
            "password": password,
        }
        for key, value in data.items():
            if key not in ["name", "type"]:
                service.configs[key] = value

        ranger.create_service(service)
        logging.info("service successfully created!")

    def _set_policy_manager(self, event):
        """Set the policy manager url in the relation databag.

        Args:
            event: relation event
        """
        relation = self.charm.model.get_relation(
            self.relation_name, event.relation.id
        )
        host = self.charm.config["external-hostname"]
        if host == "ranger-k8s":
            protocol = "http"
        else:
            protocol = "https"
        if relation:
            relation.data[self.charm.app].update(
                {"policy_manager_url": f"{protocol}://{host}:6080"}
            )

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle database requested event.

        Generate password and provide access to the Trino applictaion.

        Args:
            event: relation changed event.
        """
        if not self.charm.unit.is_leader():
            return

        data = event.relation.data[event.app]

        if not data:
            return

        ranger_auth = ("admin", self.charm.config["ranger-admin-password"])
        ranger = ranger_client.RangerClient(RANGER_URL, ranger_auth)

        try:
            self._create_ranger_service(ranger, data, event)
        except Exception as err:
            logging.debug(err)
            return

        self._set_policy_manager(event)
