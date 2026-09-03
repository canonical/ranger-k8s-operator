# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger client relation helpers."""

import logging

from apache_ranger.model.ranger_service import RangerService
from ops.charm import CharmBase
from ops.framework import Object

from literals import (
    DEFAULT_POLICIES,
    SERVICE_STAMP_PREFIX,
)
from ranger_client import RangerAPIClient, RangerAPIError

logger = logging.getLogger(__name__)


class RangerProvider(Object):
    """Defines functionality for the 'provides' side of the 'ranger-client' relation.

    Event observation is centralized in the charm; this object exposes logic methods
    invoked by the charm reconciler.
    """

    def __init__(self, charm: CharmBase, relation_name: str = "policy") -> None:
        """Construct RangerProvider object.

        Args:
            charm: The charm for which this relation is provided.
            relation_name: The name of the relation.
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.charm = charm

    def publish_policy_manager_url(self) -> None:
        """Publish the policy manager URL to active policy relations.

        Returns:
            None.
        """
        if not self.charm.unit.is_leader():
            return
        url = self.charm.resolve_policy_manager_url()
        if url is None:
            return
        for relation in self.charm.model.relations[self.relation_name]:
            relation.data[self.charm.app]["policy_manager_url"] = url

    def reconcile_services(self, client: RangerAPIClient) -> None:
        """Reconcile Ranger services for live policy relations.

        Args:
            client: The configured Ranger API client.

        Returns:
            None.

        Raises:
            RangerAPIError: If service discovery or policy lookup fails.
        """
        live = {
            relation.id: relation
            for relation in self.charm.model.relations[self.relation_name]
            if relation.active and relation.app is not None
        }
        managed = {}
        for service in client.list_services():
            username = (service.configs or {}).get("username", "")
            if username.startswith(SERVICE_STAMP_PREFIX):
                try:
                    managed[int(username[len(SERVICE_STAMP_PREFIX) :])] = service
                except ValueError:
                    continue

        self._create_live_services(client, live, managed)
        self._garbage_collect_services(client, live, managed)

    def _create_live_services(self, client: RangerAPIClient, live, managed) -> None:
        """Create services for live policy relations that do not have one.

        Args:
            client: The configured Ranger API client.
            live: Active policy relations keyed by relation ID.
            managed: Charm-managed Ranger services keyed by relation ID.

        Returns:
            None.

        Raises:
            RangerAPIError: If service lookup fails.
        """
        for relation_id, relation in live.items():
            data = relation.data[relation.app]
            if not data.get("name") or not data.get("type"):
                logger.debug("policy relation %s has not published its service", relation_id)
                continue
            if relation_id in managed:
                continue
            if client.get_service_by_name(data["name"]) is not None:
                logger.warning(
                    "service %s already exists and is not managed by relation %s",
                    data["name"],
                    relation_id,
                )
                continue
            try:
                client.create_service(self._build_service(data, relation_id))
            except RangerAPIError as error:
                logger.warning("failed to create service %s: %s", data["name"], error)

    def _garbage_collect_services(self, client: RangerAPIClient, live, managed) -> None:
        """Delete managed services whose policy relation has departed.

        Args:
            client: The configured Ranger API client.
            live: Active policy relations keyed by relation ID.
            managed: Charm-managed Ranger services keyed by relation ID.

        Returns:
            None.

        Raises:
            RangerAPIError: If policy lookup fails.
        """
        for relation_id, service in managed.items():
            if relation_id in live:
                continue
            if self._has_custom_policies(client, service.name, relation_id):
                logger.warning(
                    "service %s has non-default policies; deletion aborted", service.name
                )
                continue
            try:
                client.delete_service_by_id(service.id)
            except RangerAPIError as error:
                logger.warning("failed to delete service %s: %s", service.name, error)

    def _build_service(self, data, relation_id: int) -> RangerService:
        """Build a Ranger service from policy relation data.

        Args:
            data: The policy relation application databag.
            relation_id: The ID of the policy relation.

        Returns:
            The service definition to create in Ranger.
        """
        service = RangerService({"name": data["name"], "type": data["type"]})
        service.configs = {
            "username": f"{SERVICE_STAMP_PREFIX}{relation_id}",
            "resource.lookup.timeout.value.in.ms": self.charm.config["lookup-timeout"],
        }
        for key, value in data.items():
            if key not in ["name", "type"]:
                service.configs[key] = value
        return service

    def _has_custom_policies(
        self, client: RangerAPIClient, service_name: str, relation_id: int
    ) -> bool:
        """Determine if the service has custom policies.

        Args:
            client: The configured Ranger API client.
            service_name: The name of the Ranger service.
            relation_id: The ID of the policy relation.

        Returns:
            Whether the service contains custom policies.

        Raises:
            RangerAPIError: If policy lookup fails.
        """
        policies = client.list_service_policies(service_name)

        for policy in policies:
            if policy.name not in DEFAULT_POLICIES:
                return True

            for item in policy["policyItems"]:
                if f"{SERVICE_STAMP_PREFIX}{relation_id}" not in item["users"]:
                    return True
        return False
