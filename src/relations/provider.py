# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger client relation hooks & helpers."""


import logging

from ops.charm import CharmBase
from ops.framework import Object
from utils import log_event_handler, generate_password
from apache_ranger.client import ranger_client
from apache_ranger.model import ranger_service

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
        """Constructor for RangerProvider object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_created,
            self._on_relation_changed,
        )
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

        service = ranger_service.RangerService({'name': data["name"], 'type': data["type"]})
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

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle database requested event.

        Generate password and provide access to the Trino applictaion.
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

        relation = self.charm.model.get_relation(
            self.relation_name, event.relation.id
        )
        if relation:
            relation.data[self.charm.app].update(
                {"policy_manager_url": "http://ranger-k8s:6080"}
            )
