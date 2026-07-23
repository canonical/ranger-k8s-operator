# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino catalog state reconciliation with Ranger security zones."""

import logging
from typing import Dict, List, Set

from apache_ranger.model.ranger_policy import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
)
from apache_ranger.model.ranger_role import RangerRole
from apache_ranger.model.ranger_security_zone import (
    RangerSecurityZone,
    RangerSecurityZoneService,
)

from literals import DEFAULT_POLICIES, ZONE_ROLE_SUFFIXES
from ranger_client import RangerAPIClient, RangerAPIError

logger = logging.getLogger(__name__)


def _catalogs_to_zones(catalogs: List[Dict]) -> Set[str]:
    """Map catalog dicts to base zone names.

    Catalogs named ``<name>_developer`` are grouped with ``<name>``
    into a single zone called ``<name>``.

    Args:
        catalogs: list of catalog dicts with at least a ``"name"`` key.

    Returns:
        Set of base zone names.
    """
    zones: Set[str] = set()
    for catalog in catalogs:
        name = catalog["name"]
        if name.endswith("_developer"):
            zones.add(name[: -len("_developer")])
        else:
            zones.add(name)
    return zones


def _role_names(zone_name: str) -> List[str]:
    """Return ordered role names for a zone.

    Args:
        zone_name: the base zone / catalog name.

    Returns:
        List of role name strings.
    """
    return [f"{zone_name}{suffix}" for suffix in ZONE_ROLE_SUFFIXES]


def _build_zone(zone_name: str, service_name: str) -> RangerSecurityZone:
    """Build a security zone constraining resources to the zone catalogs.

    Args:
        zone_name: the base zone / catalog name.
        service_name: the Trino service name registered in Ranger.

    Returns:
        A ``RangerSecurityZone`` ready to be created.
    """
    zone_service = RangerSecurityZoneService(
        {
            "resources": [
                {
                    "catalog": [zone_name, f"{zone_name}_developer"],
                }
            ]
        }
    )
    return RangerSecurityZone(
        {
            "name": zone_name,
            "services": {service_name: zone_service},
            "tagServices": [],
            "adminUsers": [],
            "adminUserGroups": [],
            "adminRoles": [f"{zone_name}-admin"],
            "auditUsers": [],
            "auditUserGroups": [],
            "auditRoles": [f"{zone_name}-auditor"],
            "description": (f"Managed zone for catalogs {zone_name} and {zone_name}_developer"),
        }
    )


def _access(permission: str) -> RangerPolicyItemAccess:
    """Create an allowed access entry for ``permission``."""
    return RangerPolicyItemAccess({"type": permission, "isAllowed": True})


def _resource(values: List[str]) -> RangerPolicyResource:
    """Create a policy resource with ``values``."""
    return RangerPolicyResource({"values": values})


def _build_ro_policy(zone_name: str, service_name: str) -> RangerPolicy:
    """Build the ``default - ro - <zone>`` policy.

    Read-only access on the base catalog for all four zone roles.

    Args:
        zone_name: the base zone / catalog name.
        service_name: the Trino service name.

    Returns:
        A ``RangerPolicy`` ready to be created.
    """
    cat = zone_name
    roles = [
        f"{zone_name}-viewer",
        f"{zone_name}-editor",
        f"{zone_name}-admin",
    ]
    accesses = [_access(p) for p in ("select", "show", "use")]
    item = RangerPolicyItem({"roles": roles, "accesses": accesses})

    policy = RangerPolicy(
        {
            "service": service_name,
            "name": f"default - ro - {zone_name}",
            "resources": {"catalog": _resource([cat])},
            "additionalResources": [
                {
                    "catalog": _resource([cat]),
                    "schema": _resource(["*"]),
                },
                {
                    "catalog": _resource([cat]),
                    "schema": _resource(["*"]),
                    "table": _resource(["*"]),
                },
                {
                    "catalog": _resource([cat]),
                    "schema": _resource(["*"]),
                    "table": _resource(["*"]),
                    "column": _resource(["*"]),
                },
            ],
            "policyItems": [item],
            "zoneName": zone_name,
            "isAuditEnabled": True,
            "isEnabled": True,
        }
    )
    return policy


def _build_rw_policy(zone_name: str, service_name: str) -> RangerPolicy:
    """Build the ``default - rw - <zone>`` policy.

    Read-write access on the developer catalog for editor and admin roles.

    Args:
        zone_name: the base zone / catalog name.
        service_name: the Trino service name.

    Returns:
        A ``RangerPolicy`` ready to be created.
    """
    dev_cat = f"{zone_name}_developer"
    roles = [f"{zone_name}-editor", f"{zone_name}-admin"]
    accesses = [_access(p) for p in ("select", "show", "use", "insert", "delete")]
    item = RangerPolicyItem({"roles": roles, "accesses": accesses})

    return RangerPolicy(
        {
            "service": service_name,
            "name": f"default - rw - {zone_name}",
            "resources": {"catalog": _resource([dev_cat])},
            "additionalResources": [
                {
                    "catalog": _resource([dev_cat]),
                    "schema": _resource(["*"]),
                },
                {
                    "catalog": _resource([dev_cat]),
                    "schema": _resource(["*"]),
                    "table": _resource(["*"]),
                },
                {
                    "catalog": _resource([dev_cat]),
                    "schema": _resource(["*"]),
                    "table": _resource(["*"]),
                    "column": _resource(["*"]),
                },
            ],
            "policyItems": [item],
            "zoneName": zone_name,
            "isAuditEnabled": True,
            "isEnabled": True,
        }
    )


def _build_ddl_policy(zone_name: str, service_name: str) -> RangerPolicy:
    """Build the ``default - ddl - <zone>`` policy.

    DDL operations on the developer catalog for admin role only.

    Args:
        zone_name: the base zone / catalog name.
        service_name: the Trino service name.

    Returns:
        A ``RangerPolicy`` ready to be created.
    """
    dev_cat = f"{zone_name}_developer"
    roles = [f"{zone_name}-admin"]
    accesses = [_access(p) for p in ("alter", "create", "drop")]
    item = RangerPolicyItem({"roles": roles, "accesses": accesses})

    return RangerPolicy(
        {
            "service": service_name,
            "name": f"default - ddl - {zone_name}",
            "resources": {
                "catalog": _resource([dev_cat]),
                "schema": _resource(["*"]),
            },
            "additionalResources": [
                {
                    "catalog": _resource([dev_cat]),
                    "schema": _resource(["*"]),
                    "table": _resource(["*"]),
                },
            ],
            "policyItems": [item],
            "zoneName": zone_name,
            "isAuditEnabled": True,
            "isEnabled": True,
        }
    )


def _build_is_policy(zone_name: str, service_name: str) -> RangerPolicy:
    """Build the ``default - is - <zone>`` policy.

    Information-schema access on both catalogs for ``{USER}`` macro.

    Args:
        zone_name: the base zone / catalog name.
        service_name: the Trino service name.

    Returns:
        A ``RangerPolicy`` ready to be created.
    """
    cats = [zone_name, f"{zone_name}_developer"]
    accesses = [_access(p) for p in ("select", "show", "use")]
    item = RangerPolicyItem({"users": ["{USER}"], "accesses": accesses})

    return RangerPolicy(
        {
            "service": service_name,
            "name": f"default - is - {zone_name}",
            "resources": {"catalog": _resource(cats)},
            "additionalResources": [
                {
                    "catalog": _resource(cats),
                    "schema": _resource(["information_schema"]),
                },
                {
                    "catalog": _resource(cats),
                    "schema": _resource(["information_schema"]),
                    "table": _resource(["*"]),
                },
                {
                    "catalog": _resource(cats),
                    "schema": _resource(["information_schema"]),
                    "table": _resource(["*"]),
                    "column": _resource(["*"]),
                },
            ],
            "policyItems": [item],
            "zoneName": zone_name,
            "isAuditEnabled": True,
            "isEnabled": True,
        }
    )


_POLICY_BUILDERS = {
    "ro": _build_ro_policy,
    "rw": _build_rw_policy,
    "ddl": _build_ddl_policy,
    "is": _build_is_policy,
}


class TrinoCatalogReconciler:
    """Reconciles Ranger zones, roles, and policies with Trino catalogs.

    Attributes:
        _client: the Ranger REST API client.
        _service_name: the registered Trino service name in Ranger.
    """

    def __init__(
        self,
        client: RangerAPIClient,
        service_name: str,
    ) -> None:
        """Construct TrinoCatalogReconciler.

        Args:
            client: a configured ``RangerAPIClient``.
            service_name: the Trino service name in Ranger.
        """
        self._client = client
        self._service_name = service_name

    def reconcile(
        self,
        catalogs: List[Dict],
        *,
        strict: bool = True,
        create_enabled: bool = True,
    ) -> None:
        """Create missing Ranger resources for Trino catalogs.

        Existing zones without Ranger's auto-generated policies are considered
        complete and are never modified.

        Args:
            catalogs: list of catalog dicts (each with at least ``"name"``).
            strict: require corresponding existing roles to be empty before creating a zone.
            create_enabled: allow creation of new or incomplete zones.
        """
        desired_zones = _catalogs_to_zones(catalogs)
        logger.info("reconciling zones: desired=%s", sorted(desired_zones))

        existing_zone_names = {zone.name for zone in self._client.list_zones()}
        existing_roles = self._client.list_roles()
        existing_roles_by_name = {role.name: role for role in existing_roles}
        policies_by_zone = self._policies_by_zone(
            self._client.list_service_policies(self._service_name)
        )

        for zone_name in sorted(desired_zones):
            zone_exists = zone_name in existing_zone_names
            zone_policies = policies_by_zone.get(zone_name, [])

            if not create_enabled:
                logger.info("creation disabled; leaving zone %s unchanged", zone_name)
                continue
            if zone_exists and not self._has_auto_policies(zone_policies):
                logger.info("zone %s is already provisioned", zone_name)
                continue
            if not zone_exists and not self._can_create_zone(
                zone_name, existing_roles_by_name, strict
            ):
                continue

            self._provision_zone(
                zone_name,
                zone_exists,
                existing_roles_by_name,
                zone_policies,
            )

    @staticmethod
    def _policies_by_zone(policies: List[RangerPolicy]) -> Dict[str, List[RangerPolicy]]:
        """Group service policies by their Ranger security-zone name."""
        policies_by_zone: Dict[str, List[RangerPolicy]] = {}
        for policy in policies:
            if policy.zoneName:
                policies_by_zone.setdefault(policy.zoneName, []).append(policy)
        return policies_by_zone

    @staticmethod
    def _has_auto_policies(policies: List[RangerPolicy]) -> bool:
        """Return whether Ranger's zone-creation policies are present."""
        # DEFAULT_POLICIES must exactly match Ranger's auto-policy names for the done-marker/purge.
        return any(policy.name in DEFAULT_POLICIES for policy in policies)

    def _can_create_zone(
        self,
        zone_name: str,
        existing_roles_by_name: Dict[str, RangerRole],
        strict: bool,
    ) -> bool:
        """Check whether existing corresponding roles permit zone creation."""
        if not strict:
            logger.warning(
                "strict role gate disabled; creating zone %s in fail-open mode", zone_name
            )
            return True

        populated_roles = [
            role_name
            for role_name in _role_names(zone_name)
            if (role := existing_roles_by_name.get(role_name))
            and (role.users or role.groups or role.roles)
        ]
        if populated_roles:
            logger.warning(
                "catalog %s left unprovisioned because roles are populated: %s",
                zone_name,
                ", ".join(populated_roles),
            )
            return False
        return True

    def _provision_zone(
        self,
        zone_name: str,
        zone_exists: bool,
        existing_roles_by_name: Dict[str, RangerRole],
        zone_policies: List[RangerPolicy],
    ) -> None:
        """Create the missing resources for a new or interrupted zone."""
        self._ensure_roles(zone_name, existing_roles_by_name)
        if not zone_exists and not self._create_zone(zone_name):
            return
        self._create_missing_policies(zone_name, zone_policies)
        current_zone_policies = self._client.list_policies(zone_name, self._service_name)
        self._purge_auto_policies(zone_name, current_zone_policies)

    def _ensure_roles(self, zone_name: str, existing_roles_by_name: Dict[str, RangerRole]) -> None:
        """Create any missing roles for the given zone.

        Args:
            zone_name: the base zone / catalog name.
            existing_roles_by_name: roles already in Ranger, keyed by name.
        """
        for role_name in _role_names(zone_name):
            if role_name not in existing_roles_by_name:
                role = RangerRole({"name": role_name})
                try:
                    self._client.create_role(role)
                except RangerAPIError as exc:
                    logger.warning(
                        "failed to create role %s: %s",
                        role_name,
                        exc.message,
                    )
                    continue
                existing_roles_by_name[role_name] = role
                logger.info("created role %s", role_name)

    def _create_zone(self, zone_name: str) -> bool:
        """Create a zone and report whether creation succeeded.

        Args:
            zone_name: the base zone / catalog name.

        Returns:
            Whether the zone was created.
        """
        try:
            self._client.create_zone(_build_zone(zone_name, self._service_name))
        except RangerAPIError as exc:
            logger.warning(
                "failed to create zone %s: %s",
                zone_name,
                exc.message,
            )
            return False
        logger.info("created zone %s", zone_name)
        return True

    def _create_missing_policies(self, zone_name: str, zone_policies: List[RangerPolicy]) -> None:
        """Create managed default policies that do not already exist."""
        existing_policy_names = {policy.name for policy in zone_policies}
        for suffix, builder in _POLICY_BUILDERS.items():
            policy_name = f"default - {suffix} - {zone_name}"
            if policy_name in existing_policy_names:
                continue
            try:
                self._client.create_policy(builder(zone_name, self._service_name))
            except RangerAPIError as exc:
                logger.warning(
                    "failed to create policy %s: %s",
                    policy_name,
                    exc.message,
                )
                continue
            logger.info("created policy %s", policy_name)

    def _purge_auto_policies(self, zone_name: str, zone_policies: List[RangerPolicy]) -> None:
        """Delete Ranger auto-generated policies for a zone.

        Args:
            zone_name: the base zone / catalog name.
            zone_policies: the current policies for this zone.
        """
        for policy in zone_policies:
            if policy.name not in DEFAULT_POLICIES:
                continue
            try:
                self._client.delete_policy_by_id(policy.id)
            except RangerAPIError as exc:
                logger.warning(
                    "failed to delete auto-policy %s (id=%s) from zone %s: %s",
                    policy.name,
                    policy.id,
                    zone_name,
                    exc.message,
                )
                continue
            logger.info(
                "deleted auto-generated policy %s (id=%s) from zone %s",
                policy.name,
                policy.id,
                zone_name,
            )
