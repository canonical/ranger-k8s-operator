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

from literals import ADMIN_USER, DEFAULT_POLICY_SUFFIXES, ZONE_ROLE_SUFFIXES
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


def _default_policy_names(zone_name: str) -> Set[str]:
    """Return the set of default policy names for a zone.

    Args:
        zone_name: the base zone / catalog name.

    Returns:
        Set of default policy name strings.
    """
    return {
        f"default - {suffix} - {zone_name}"
        for suffix in DEFAULT_POLICY_SUFFIXES
    }


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
            "adminUsers": [ADMIN_USER],
            "adminUserGroups": [],
            "adminRoles": [],
            "auditUsers": [ADMIN_USER],
            "auditUserGroups": [],
            "auditRoles": [],
            "description": (
                f"Managed zone for catalogs {zone_name} "
                f"and {zone_name}_developer"
            ),
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
    roles = _role_names(zone_name)
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
    accesses = [
        _access(p) for p in ("select", "show", "use", "insert", "delete")
    ]
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

    def reconcile(self, catalogs: List[Dict]) -> None:
        """Run the full reconciliation loop.

        Args:
            catalogs: list of catalog dicts (each with at least ``"name"``).
        """
        desired_zones = _catalogs_to_zones(catalogs)
        logger.info("reconciling zones: desired=%s", sorted(desired_zones))

        existing_zones = self._client.list_zones()
        existing_zone_names = {z.name for z in existing_zones}

        existing_roles = self._client.list_roles()
        existing_role_names = {r.name for r in existing_roles}

        # --- Create missing roles and zones ---
        for zone_name in sorted(desired_zones):
            self._ensure_roles(zone_name, existing_role_names)
            if zone_name not in existing_zone_names:
                zone = _build_zone(zone_name, self._service_name)
                self._client.create_zone(zone)
                logger.info("created zone %s", zone_name)

        # --- Ensure default policies for desired zones ---
        for zone_name in sorted(desired_zones):
            self._ensure_policies(zone_name)

        # --- Clean up stale zones ---
        stale_zones = existing_zone_names - desired_zones
        for zone_name in sorted(stale_zones):
            self._cleanup_zone(zone_name)

    def _ensure_roles(
        self, zone_name: str, existing_role_names: Set[str]
    ) -> None:
        """Create any missing roles for the given zone.

        Args:
            zone_name: the base zone / catalog name.
            existing_role_names: set of role names already in Ranger.
        """
        for role_name in _role_names(zone_name):
            if role_name not in existing_role_names:
                role = RangerRole({"name": role_name})
                self._client.create_role(role)
                existing_role_names.add(role_name)
                logger.info("created role %s", role_name)

    def _ensure_policies(self, zone_name: str) -> None:
        """Ensure default policies exist and are correct for a zone.

        Args:
            zone_name: the base zone / catalog name.
        """
        policies = self._client.list_policies(zone_name, self._service_name)
        existing_by_name = {p.name: p for p in policies}

        for suffix, builder in _POLICY_BUILDERS.items():
            policy_name = f"default - {suffix} - {zone_name}"
            desired = builder(zone_name, self._service_name)

            if policy_name not in existing_by_name:
                self._client.create_policy(desired)
                logger.info("created policy %s", policy_name)
            else:
                existing = existing_by_name[policy_name]
                if self._policy_needs_update(existing, desired):
                    desired.id = existing.id
                    self._client.update_policy(
                        self._service_name, policy_name, desired
                    )
                    logger.info("updated policy %s", policy_name)

    def _cleanup_zone(self, zone_name: str) -> None:
        """Remove a stale zone if it only contains default policies.

        Zones with custom (non-default) policies are left untouched.

        Args:
            zone_name: the zone to potentially remove.
        """
        policies = self._client.list_policies(zone_name, self._service_name)
        default_names = _default_policy_names(zone_name)

        for policy in policies:
            if policy.name not in default_names:
                logger.warning(
                    "zone %s has custom policy %s, skipping cleanup",
                    zone_name,
                    policy.name,
                )
                return

        self._client.delete_zone(zone_name)
        logger.info("deleted stale zone %s", zone_name)

        for role_name in _role_names(zone_name):
            try:
                self._client.delete_role(role_name)
                logger.info("deleted stale role %s", role_name)
            except RangerAPIError:
                logger.warning(
                    "could not delete role %s, it may not exist",
                    role_name,
                )

    @staticmethod
    def _policy_needs_update(
        existing: RangerPolicy, desired: RangerPolicy
    ) -> bool:
        """Check whether an existing policy differs from the desired state.

        Compares resource blocks and policy items (roles/users/accesses).

        Args:
            existing: the current policy from Ranger.
            desired: the desired policy definition.

        Returns:
            True if the existing policy needs to be updated.
        """
        if _serialise_resources(existing) != _serialise_resources(desired):
            return True
        if _serialise_items(existing.policyItems) != _serialise_items(
            desired.policyItems
        ):
            return True
        return False


def _serialise_resources(policy: RangerPolicy) -> list:
    """Serialise a policy's resource blocks into a comparable structure.

    Args:
        policy: the ``RangerPolicy`` to serialise.

    Returns:
        A sorted list of frozen resource representations.
    """
    blocks = []
    if policy.resources:
        blocks.append(_freeze_resource_block(policy.resources))
    for block in policy.additionalResources or []:
        blocks.append(_freeze_resource_block(block))
    return sorted(blocks)


def _freeze_resource_block(block: dict) -> tuple:
    """Convert a resource block dict into a frozen comparable tuple.

    Args:
        block: dict mapping resource key to ``RangerPolicyResource``
            or dict.

    Returns:
        A sorted tuple of (key, frozenset(values)) pairs.
    """
    items = []
    for key, res in sorted(block.items()):
        if isinstance(res, RangerPolicyResource):
            values = frozenset(res.values or [])
        elif isinstance(res, dict):
            values = frozenset(res.get("values", []))
        else:
            values = frozenset()
        items.append((key, values))
    return tuple(items)


def _serialise_items(items: list) -> set:
    """Serialise policy items into a comparable frozen set.

    Args:
        items: list of ``RangerPolicyItem`` or dicts.

    Returns:
        A frozenset of serialised item tuples.
    """
    result = set()
    for item in items or []:
        if isinstance(item, RangerPolicyItem):
            users = frozenset(item.users or [])
            roles = frozenset(item.roles or [])
            accesses = frozenset(
                (a.type, a.isAllowed) for a in (item.accesses or [])
            )
        else:
            users = frozenset(item.get("users", []))
            roles = frozenset(item.get("roles", []))
            accesses = frozenset(
                (a.get("type"), a.get("isAllowed", True))
                for a in item.get("accesses", [])
            )
        result.add((users, roles, accesses))
    return result
