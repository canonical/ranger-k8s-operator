# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger client relation hooks & helpers."""


import logging

from apache_ranger.client import ranger_client
from apache_ranger.exceptions import RangerServiceException
from apache_ranger.model import ranger_service
from ops.charm import CharmBase
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus

from literals import (
    ADMIN_USER,
    APPLICATION_PORT,
    DEFAULT_POLICIES,
    LOCALHOST_URL,
)
from utils import log_event_handler

logger = logging.getLogger(__name__)


class RangerProvider(Object):
    """Defines functionality for the 'provides' side of the 'ranger-client' relation.

    Hook events observed:
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
        self.framework.observe(
            charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

        self.charm = charm

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle policy relation changed event.

        Create Ranger service for related application.

        Args:
            event: relation changed event.
        """
        if not self.charm.unit.is_leader():
            return

        data = event.relation.data[event.app]

        if not data:
            return

        self.charm.unit.status = MaintenanceStatus("Adding policy relation")

        logger.info("creating service")
        try:
            ranger = self._create_ranger_client()
            created_service = self._create_ranger_service(ranger, data, event)
        except RangerServiceException:
            event.defer()
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to create a service:"
            )
            return
        except Exception:
            self.charm.unit.status = BlockedStatus("Unable to create service")
            logger.exception(
                "An error occurred while creating the ranger service:"
            )
            return

        if not created_service:
            return

        services = self.charm._state.services or {}
        services[f"relation_{event.relation.id}"] = created_service.id

        self.charm._state.services = services
        self._set_policy_manager(event)
        self.charm.unit.status = ActiveStatus()

    @log_event_handler(logger)
    def _on_relation_broken(self, event):
        """Handle on relation broken event.

        Args:
            event: on relation broken event.
        """
        if not self.charm.unit.is_leader():
            return

        if f"relation_{event.relation.id}" not in self.charm._state.services:
            return

        try:
            service_id = self.charm._state.services[
                f"relation_{event.relation.id}"
            ]
            self._delete_ranger_service(service_id, event.relation.id)
        except RangerServiceException:
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to delete a service:"
            )
            return
        except Exception:
            self.charm.unit.status = BlockedStatus("Unable to delete service")
            logger.exception(
                "An error occurred while deleting the ranger service:"
            )
            return

        existing_services = self.charm._state.services
        del existing_services[f"relation_{event.relation.id}"]
        self.charm._state.services = existing_services

    def _create_ranger_service(self, ranger, data, event):
        """Create application service in Ranger.

        Args:
            ranger: ranger client
            data: relation data
            event: relation event

        Returns:
            created_service: the object for the created service
        """
        service_name = data["name"]
        retrieved_service = ranger.get_service(service_name)
        if retrieved_service is not None:
            logger.info(
                f"Service {service_name!r} not created as it already exists."
            )
            return None

        service = ranger_service.RangerService(
            {"name": service_name, "type": data["type"]}
        )
        service.configs = {
            "username": f"relation_id_{event.relation.id}",
            "resource.lookup.timeout.value.in.ms": self.charm.config[
                "lookup-timeout"
            ],
        }
        for key, value in data.items():
            if key not in ["name", "type"]:
                service.configs[key] = value

        created_service = ranger.create_service(service)

        new_service = ranger.get_service(service_name)
        if new_service is None:
            logger.info("Service unable to be created, deferring event.")
            event.defer()
            return None
        return created_service

    def _set_policy_manager(self, event):
        """Set the policy manager url in the relation databag.

        Args:
            event: relation event
        """
        relation = self.charm.model.get_relation(
            self.relation_name, event.relation.id
        )
        data = event.relation.data[event.app]

        if relation:
            relation.data[self.charm.app].update(
                {
                    "policy_manager_url": self.charm.config["policy-mgr-url"],
                    "service_name": data["name"],
                }
            )

    def _create_ranger_client(self):
        """Prepare Ranger client.

        Returns:
            ranger: ranger client
        """
        ranger_auth = (ADMIN_USER, self.charm.config["ranger-admin-password"])
        ranger_url = f"{LOCALHOST_URL}:{APPLICATION_PORT}"
        ranger = ranger_client.RangerClient(ranger_url, ranger_auth)
        return ranger

    def _delete_ranger_service(self, service_id, relation_id):
        """Delete service in Ranger.

        Args:
            service_id: the ID of the service to delete
            relation_id: the id of the relation
        """
        ranger = self._create_ranger_client()
        retrieved_service = ranger.get_service_by_id(service_id)

        if retrieved_service is None:
            return

        if self._has_custom_policies(
            ranger, retrieved_service.name, relation_id
        ):
            logger.warning(
                f"Service {retrieved_service.name} has non-default policies defined. Deletion aborted."
            )
            return
        ranger.delete_service_by_id(service_id)

    def _has_custom_policies(self, ranger, service_name, relation_id):
        """Determine if the service has custom policies.

        Args:
            ranger: ranger client
            service_name: the name of the ranger service
            relation_id: the id of the relation

        Returns:
            bool: if the service contains custom policies
        """
        policies = ranger.get_policies_in_service(service_name)

        for policy in policies:
            if policy["name"] not in DEFAULT_POLICIES:
                return True

            for item in policy["policyItems"]:
                if f"relation_id_{relation_id}" not in item["users"]:
                    return True
        return False
