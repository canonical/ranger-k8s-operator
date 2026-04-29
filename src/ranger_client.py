# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger REST API client wrapper."""

import logging
from typing import List, Optional, Tuple

from apache_ranger.client.ranger_client import RangerClient
from apache_ranger.exceptions import RangerServiceException
from apache_ranger.model.ranger_policy import RangerPolicy
from apache_ranger.model.ranger_role import RangerRole
from apache_ranger.model.ranger_security_zone import RangerSecurityZone
from apache_ranger.model.ranger_service import RangerService

from literals import ADMIN_USER

logger = logging.getLogger(__name__)


class RangerAPIError(Exception):
    """Raised when a Ranger REST API call fails."""

    def __init__(self, message: str) -> None:
        """Construct RangerAPIError.

        Args:
            message: description of the error.
        """
        self.message = message
        super().__init__(self.message)


class RangerAPIClient:
    """Client for Ranger REST API interactions.

    Wraps the apache-ranger library's RangerClient with consistent
    error handling and logging.

    Attributes:
        _client: underlying apache-ranger RangerClient instance.
    """

    def __init__(self, url: str, auth: Tuple[str, str]) -> None:
        """Construct RangerAPIClient.

        Args:
            url: base URL for the Ranger admin service
                (e.g. ``http://localhost:6080``).
            auth: tuple of ``(username, password)`` for basic authentication.
        """
        self._client = RangerClient(url, auth)
        self._client.client_http.session.hooks["response"].append(
            RangerAPIClient._log_error_response
        )

    @staticmethod
    def _log_error_response(response, *args, **kwargs):
        """Log details of non-2xx HTTP responses for debugging."""
        if not response.ok:
            logger.debug(
                "HTTP error: %s %s -> status=%s, headers=%s, body=%r",
                response.request.method,
                response.request.url,
                response.status_code,
                dict(response.headers),
                response.text,
            )

    def list_services_by_type(self, service_type: str) -> List[RangerService]:
        """List services filtered by type.

        ``GET /service/public/v2/api/service?serviceType=<service_type>``

        Args:
            service_type: the Ranger service type (e.g. ``"trino"``).

        Returns:
            List of matching ``RangerService`` objects.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("listing services with type=%s", service_type)
        try:
            services: Optional[
                List[RangerService]
            ] = self._client.find_services({"serviceType": service_type})
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to list services by type {service_type!r}: {exc}"
            ) from exc
        return services or []

    def list_zones(self) -> List[RangerSecurityZone]:
        """List all security zones.

        ``GET /service/public/v2/api/zone``

        Returns:
            List of ``RangerSecurityZone`` objects.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("listing security zones")
        try:
            zones: Optional[
                List[RangerSecurityZone]
            ] = self._client.find_security_zones()
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to list security zones: {exc}"
            ) from exc
        return zones or []

    def get_zone(self, zone_name: str) -> RangerSecurityZone:
        """Get a security zone by name.

        ``GET /service/public/v2/api/zone/name/<zone_name>``

        Args:
            zone_name: name of the security zone.

        Returns:
            The matching ``RangerSecurityZone``.

        Raises:
            RangerAPIError: if the zone is not found or the call fails.
        """
        logger.info("getting security zone %s", zone_name)
        try:
            zone: Optional[
                RangerSecurityZone
            ] = self._client.get_security_zone(zone_name)
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to get security zone {zone_name!r}: {exc}"
            ) from exc
        if zone is None:
            raise RangerAPIError(f"Security zone {zone_name!r} not found")
        return zone

    def create_zone(self, zone: RangerSecurityZone) -> RangerSecurityZone:
        """Create a security zone.

        ``POST /service/public/v2/api/zone``

        Args:
            zone: the zone definition to create.

        Returns:
            The created ``RangerSecurityZone``.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("creating security zone %s", zone.name)
        try:
            created: Optional[
                RangerSecurityZone
            ] = self._client.create_security_zone(zone)
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to create security zone {zone.name!r}: {exc}"
            ) from exc
        if created is None:
            raise RangerAPIError(
                f"Failed to create security zone {zone.name!r}: "
                "no response from server"
            )
        return created

    def delete_zone(self, zone_name: str) -> None:
        """Delete a security zone by name.

        ``DELETE /service/public/v2/api/zone/name/<zone_name>``

        This also deletes all policies within the zone.

        Args:
            zone_name: name of the security zone to delete.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("deleting security zone %s", zone_name)
        try:
            self._client.delete_security_zone(zone_name)
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to delete security zone {zone_name!r}: {exc}"
            ) from exc

    def list_policies(
        self, zone_name: str, service_name: str
    ) -> List[RangerPolicy]:
        """List policies filtered by zone and service name.

        ``GET /service/public/v2/api/policy?zoneName=<z>&serviceName=<s>``

        Args:
            zone_name: name of the security zone.
            service_name: name of the Ranger service.

        Returns:
            List of matching ``RangerPolicy`` objects.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info(
            "listing policies for zone=%s service=%s",
            zone_name,
            service_name,
        )
        try:
            policies: Optional[
                List[RangerPolicy]
            ] = self._client.find_policies(
                {
                    "zoneName": zone_name,
                    "serviceName": service_name,
                }
            )
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to list policies for zone={zone_name!r} "
                f"service={service_name!r}: {exc}"
            ) from exc
        return policies or []

    def get_policy(self, service_name: str, policy_name: str) -> RangerPolicy:
        """Get a policy by service and policy name.

        ``GET /service/public/v2/api/service/<svc>/policy/<policy>``

        Args:
            service_name: name of the Ranger service.
            policy_name: name of the policy.

        Returns:
            The matching ``RangerPolicy``.

        Raises:
            RangerAPIError: if the policy is not found or the call fails.
        """
        logger.info(
            "getting policy %s in service %s", policy_name, service_name
        )
        try:
            policy: Optional[RangerPolicy] = self._client.get_policy(
                service_name, policy_name
            )
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to get policy {policy_name!r} "
                f"in service {service_name!r}: {exc}"
            ) from exc
        if policy is None:
            raise RangerAPIError(
                f"Policy {policy_name!r} not found "
                f"in service {service_name!r}"
            )
        return policy

    def create_policy(self, policy: RangerPolicy) -> RangerPolicy:
        """Create a policy.

        ``POST /service/public/v2/api/policy``

        Args:
            policy: the policy definition to create.

        Returns:
            The created ``RangerPolicy``.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("creating policy %s", policy.name)
        try:
            created: Optional[RangerPolicy] = self._client.create_policy(
                policy
            )
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to create policy {policy.name!r}: {exc}"
            ) from exc
        if created is None:
            raise RangerAPIError(
                f"Failed to create policy {policy.name!r}: "
                "no response from server"
            )
        return created

    def update_policy(
        self,
        service_name: str,
        policy_name: str,
        policy: RangerPolicy,
    ) -> RangerPolicy:
        """Update a policy by service and policy name.

        ``PUT /service/public/v2/api/service/<svc>/policy/<policy>``

        Args:
            service_name: name of the Ranger service.
            policy_name: name of the policy to update.
            policy: the updated policy definition.

        Returns:
            The updated ``RangerPolicy``.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info(
            "updating policy %s in service %s",
            policy_name,
            service_name,
        )
        try:
            updated: Optional[RangerPolicy] = self._client.update_policy(
                service_name, policy_name, policy
            )
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to update policy {policy_name!r} "
                f"in service {service_name!r}: {exc}"
            ) from exc
        if updated is None:
            raise RangerAPIError(
                f"Failed to update policy {policy_name!r} "
                f"in service {service_name!r}: no response from server"
            )
        return updated

    def list_roles(self) -> List[RangerRole]:
        """List all roles.

        ``GET /service/public/v2/api/roles``

        Returns:
            List of ``RangerRole`` objects.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("listing roles")
        try:
            roles: Optional[List[RangerRole]] = self._client.find_roles()
        except RangerServiceException as exc:
            raise RangerAPIError(f"Failed to list roles: {exc}") from exc
        return roles or []

    def get_role(self, role_name: str) -> RangerRole:
        """Get a role by name.

        ``GET /service/public/v2/api/roles/name/<role_name>``

        Args:
            role_name: name of the role.

        Returns:
            The matching ``RangerRole``.

        Raises:
            RangerAPIError: if the role is not found or the call fails.
        """
        logger.info("getting role %s", role_name)
        try:
            role: Optional[RangerRole] = self._client.get_role(
                role_name, ADMIN_USER, ""
            )
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to get role {role_name!r}: {exc}"
            ) from exc
        if role is None:
            raise RangerAPIError(f"Role {role_name!r} not found")
        return role

    def create_role(self, role: RangerRole) -> RangerRole:
        """Create a role.

        ``POST /service/public/v2/api/roles``

        Args:
            role: the role definition to create.

        Returns:
            The created ``RangerRole``.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("creating role %s", role.name)
        try:
            created: Optional[RangerRole] = self._client.create_role("", role)
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to create role {role.name!r}: {exc}"
            ) from exc
        if created is None:
            raise RangerAPIError(
                f"Failed to create role {role.name!r}: "
                "no response from server"
            )
        return created

    def delete_policy_by_id(self, policy_id: int) -> None:
        """Delete a policy by its ID.

        ``DELETE /service/public/v2/api/policy/<id>``

        Args:
            policy_id: numeric ID of the policy to delete.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("deleting policy id=%s", policy_id)
        try:
            self._client.delete_policy_by_id(policy_id)
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to delete policy id={policy_id}: {exc}"
            ) from exc

    def delete_role(self, role_name: str) -> None:
        """Delete a role by name.

        ``DELETE /service/public/v2/api/roles/name/<role_name>``

        Args:
            role_name: name of the role to delete.

        Raises:
            RangerAPIError: if the API call fails.
        """
        logger.info("deleting role %s", role_name)
        try:
            self._client.delete_role(role_name, ADMIN_USER, "")
        except RangerServiceException as exc:
            raise RangerAPIError(
                f"Failed to delete role {role_name!r}: {exc}"
            ) from exc
