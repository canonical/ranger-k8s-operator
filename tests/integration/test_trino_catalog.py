# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the trino-catalog relation."""

import logging
import time

import jubilant
import pytest
import yaml
from apache_ranger.client.ranger_client import RangerClient
from apache_ranger.model.ranger_policy import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
)
from apache_ranger.model.ranger_role import RangerRole

from integration.helpers import (
    APP_NAME,
    RANGER_AUTH,
    TRINO_NAME,
    TRINO_SERVICE,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)

CATALOG_NAME = "testcat"
STRICT_CATALOG_NAME = "strictcat"
FROZEN_CATALOG_NAME = "frozencat"
DEFAULT_POLICY_NAMES = {
    f"default - {suffix} - {CATALOG_NAME}" for suffix in ("ro", "rw", "ddl", "is")
}
MANAGED_ROLE_NAMES = {
    f"{CATALOG_NAME}-{suffix}" for suffix in ("viewer", "editor", "admin", "auditor")
}

POLL_INTERVAL = 15
POLL_TIMEOUT = 300
RECONCILE_CYCLES = 180

REPLICA_SECRET = """\
ro:
  user: test_ro_user
  password: roPassw0rd
rw:
  user: test_rw_user
  password: rwPassw0rd
"""  # nosec B105


def _get_ranger_client(juju: jubilant.Juju) -> RangerClient:
    """Build a RangerClient pointed at the Ranger unit."""
    url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
    return RangerClient(url, RANGER_AUTH)


def _poll_zone(juju, zone_name):
    """Poll Ranger until a zone is present.

    Args:
        juju: Jubilant Juju object.
        zone_name: Name of the security zone to look for.

    Returns:
        The matching zone.

    Raises:
        TimeoutError: if the condition is not met within POLL_TIMEOUT.
    """
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        ranger = _get_ranger_client(juju)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        if zone_name in zone_names:
            return ranger.get_security_zone(zone_name)
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Zone {zone_name!r} did not appear within {POLL_TIMEOUT}s")


def _build_catalog_config(juju, secret_id, catalog_names=(CATALOG_NAME,)):
    """Build the catalog-config YAML string for Trino.

    Dynamically resolves the PostgreSQL JDBC URL and database name from
    the deployed postgresql-k8s application.

    Args:
        juju: Jubilant Juju object.
        secret_id: Juju secret ID containing replica credentials.
        catalog_names: Names exposed by Trino for these catalogs.

    Returns:
        A valid YAML string for the trino-k8s catalog-config option.
    """
    model_name = juju.status().model.name
    pg_url = f"jdbc:postgresql://postgresql-k8s-primary.{model_name}.svc.cluster.local:5432"

    db_name = CATALOG_NAME

    config = {
        "catalogs": {
            catalog_name: {
                "backend": "test-backend",
                "database": db_name,
                "secret-id": secret_id,
            }
            for catalog_name in catalog_names
        },
        "backends": {
            "test-backend": {
                "connector": "postgresql",
                "url": pg_url,
                "params": "targetServerType=primary",
                "config": (
                    "case-insensitive-name-matching=true\n"
                    "decimal-mapping=allow_overflow\n"
                    "decimal-rounding-mode=HALF_UP\n"
                ),
            },
        },
    }
    return yaml.dump(config, default_flow_style=False)


def _create_and_grant_secret(juju):
    """Create a Juju user secret with replica credentials and grant it to Trino.

    Returns:
        The secret ID string.
    """
    secret_name = "test-pg-credentials"  # nosec B105
    secret_uri = juju.add_secret(secret_name, {"replicas": REPLICA_SECRET})
    secret_id = secret_uri.unique_identifier
    logger.info("created secret %s with id %s", secret_name, secret_id)

    juju.grant_secret(secret_name, TRINO_NAME)

    return secret_id


def _set_catalog_config(juju, secret_id, catalog_names=(CATALOG_NAME,)):
    """Set catalog-config on the Trino charm."""
    config_yaml = _build_catalog_config(juju, secret_id, catalog_names)
    juju.config(TRINO_NAME, {"catalog-config": config_yaml})
    wait_for_apps(juju, [TRINO_NAME, APP_NAME], status="active", timeout=1500)


def _clear_catalog_config(juju):
    """Remove all catalogs from Trino by clearing catalog-config."""
    empty_config = ""
    juju.config(TRINO_NAME, {"catalog-config": empty_config})
    wait_for_apps(juju, [TRINO_NAME, APP_NAME], status="active", timeout=1500)


def _policies_in_zone(ranger, zone_name):
    """Return policies in a Ranger zone keyed by name."""
    policies = ranger.find_policies({"zoneName": zone_name, "serviceName": TRINO_SERVICE}) or []
    return {policy.name: policy for policy in policies}


def _roles_by_name(ranger):
    """Return all Ranger roles keyed by name."""
    return {role.name: role for role in ranger.find_roles() or []}


@pytest.fixture(name="deploy_trino_catalog", scope="module")
def deploy_trino_catalog_fixture(juju: jubilant.Juju, deploy):
    """Deploy Trino and configure the trino-catalog relation with Ranger.

    Reuses the base `deploy` fixture (Ranger + PostgreSQL already active).
    Deploys trino-k8s, relates on both `policy` and `trino-catalog`,
    creates a Juju secret with credentials, and sets `catalog-config`.

    Args:
        juju: Jubilant Juju object.
        deploy: The base deploy fixture (Ranger + PostgreSQL).

    Returns:
        The Juju secret ID used for catalog configuration.
    """
    trino_config = {
        "charm-function": "all",
        "ranger-service-name": TRINO_SERVICE,
    }

    # Deploy Trino only if not already present
    if TRINO_NAME not in juju.status().apps:
        juju.deploy(
            TRINO_NAME,
            channel="edge",
            config=trino_config,
            trust=True,
        )
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

    # Integrate policy if not already related
    juju.integrate(f"{APP_NAME}:policy", f"{TRINO_NAME}:policy")
    wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

    # Integrate trino-catalog
    juju.integrate(f"{APP_NAME}:trino-catalog", f"{TRINO_NAME}:trino-catalog")
    wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

    # Create secret and configure catalog
    secret_id = _create_and_grant_secret(juju)
    _set_catalog_config(juju, secret_id)

    return secret_id


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy_trino_catalog")
class TestTrinoCatalogRelation:
    """Integration tests for the trino-catalog relation lifecycle."""

    def test_relation_active(self, juju: jubilant.Juju):
        """Validate Ranger and Trino are active after relating on both interfaces."""
        status = juju.status()
        ranger_status = status.apps[APP_NAME].units[f"{APP_NAME}/0"].workload_status.current
        trino_status = status.apps[TRINO_NAME].units[f"{TRINO_NAME}/0"].workload_status.current
        assert ranger_status == "active"
        assert trino_status == "active"

    def test_zone_created(self, juju: jubilant.Juju):
        """Validate that adding a catalog creates a security zone in Ranger."""
        zone = _poll_zone(juju, CATALOG_NAME)
        assert zone is not None
        logger.info("zone: %s", zone)

    def test_zone_default_policies(self, juju: jubilant.Juju):
        """Validate that the new zone contains exactly the four default policies."""
        ranger = _get_ranger_client(juju)
        policy_names = set(_policies_in_zone(ranger, CATALOG_NAME))
        logger.info("policies in zone %s: %s", CATALOG_NAME, policy_names)
        assert policy_names == DEFAULT_POLICY_NAMES

    def test_existing_resources_are_not_updated_or_deleted(
        self, juju: jubilant.Juju, deploy_trino_catalog
    ):
        """Validate reconciliation preserves existing zones, roles, and policies."""
        ranger = _get_ranger_client(juju)
        zone = ranger.get_security_zone(CATALOG_NAME)
        roles = _roles_by_name(ranger)
        policies = _policies_in_zone(ranger, CATALOG_NAME)
        assert MANAGED_ROLE_NAMES <= set(roles)

        managed_policy = policies[f"default - ro - {CATALOG_NAME}"]
        managed_policy.description = "manually customized"
        ranger.update_policy_by_id(managed_policy.id, managed_policy)

        custom_policy = RangerPolicy(
            {
                "service": TRINO_SERVICE,
                "name": f"custom-test-policy-{CATALOG_NAME}",
                "resources": {
                    "catalog": RangerPolicyResource({"values": [CATALOG_NAME]}),
                },
                "policyItems": [
                    RangerPolicyItem(
                        {
                            "users": ["admin"],
                            "accesses": [
                                RangerPolicyItemAccess({"type": "select", "isAllowed": True})
                            ],
                        }
                    )
                ],
                "zoneName": CATALOG_NAME,
                "isAuditEnabled": True,
                "isEnabled": True,
            }
        )
        custom_policy = ranger.create_policy(custom_policy)

        _clear_catalog_config(juju)
        time.sleep(RECONCILE_CYCLES)

        ranger = _get_ranger_client(juju)
        assert ranger.get_security_zone(CATALOG_NAME).id == zone.id

        current_roles = _roles_by_name(ranger)
        assert {name: current_roles[name].id for name in MANAGED_ROLE_NAMES} == {
            name: roles[name].id for name in MANAGED_ROLE_NAMES
        }

        current_policies = _policies_in_zone(ranger, CATALOG_NAME)
        assert set(current_policies) == set(policies) | {custom_policy.name}
        assert current_policies[managed_policy.name].id == managed_policy.id
        assert current_policies[managed_policy.name].description == "manually customized"
        assert current_policies[custom_policy.name].id == custom_policy.id

        _set_catalog_config(juju, deploy_trino_catalog)
        time.sleep(RECONCILE_CYCLES)

        policies_after_reconciliation = _policies_in_zone(_get_ranger_client(juju), CATALOG_NAME)
        assert (
            policies_after_reconciliation[managed_policy.name].description == "manually customized"
        )
        assert (
            policies_after_reconciliation[custom_policy.name].id
            == current_policies[custom_policy.name].id
        )

    def test_strict_gate_adopts_existing_roles_when_disabled(
        self, juju: jubilant.Juju, deploy_trino_catalog
    ):
        """Validate strict mode blocks populated roles and fail-open mode adopts them."""
        ranger = _get_ranger_client(juju)
        populated_role = ranger.create_role(
            "",
            RangerRole(
                {
                    "name": f"{STRICT_CATALOG_NAME}-viewer",
                    "users": [{"name": "existing-user"}],
                }
            ),
        )

        _set_catalog_config(juju, deploy_trino_catalog, (CATALOG_NAME, STRICT_CATALOG_NAME))
        time.sleep(RECONCILE_CYCLES)

        zones = ranger.find_security_zones() or []
        assert STRICT_CATALOG_NAME not in {zone.name for zone in zones}

        juju.config(APP_NAME, {"enforce-strict-reconciliation": False})
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)
        _poll_zone(juju, STRICT_CATALOG_NAME)

        adopted_role = _roles_by_name(_get_ranger_client(juju))[populated_role.name]
        assert adopted_role.id == populated_role.id
        assert adopted_role.users

    def test_reconciliation_toggle_freezes_creation(
        self, juju: jubilant.Juju, deploy_trino_catalog
    ):
        """Validate disabling reconciliation prevents creation of a new zone."""
        juju.config(APP_NAME, {"toggle-catalog-reconciliation": False})
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

        _set_catalog_config(
            juju,
            deploy_trino_catalog,
            (CATALOG_NAME, STRICT_CATALOG_NAME, FROZEN_CATALOG_NAME),
        )
        time.sleep(RECONCILE_CYCLES)

        zones = _get_ranger_client(juju).find_security_zones() or []
        assert FROZEN_CATALOG_NAME not in {zone.name for zone in zones}

    def test_relation_removal_keeps_ranger_objects(self, juju: jubilant.Juju):
        """Validate breaking the relation leaves the Ranger resources in place."""
        juju.remove_relation(f"{APP_NAME}:trino-catalog", f"{TRINO_NAME}:trino-catalog")
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)
        time.sleep(RECONCILE_CYCLES)

        ranger = _get_ranger_client(juju)
        assert ranger.get_security_zone(CATALOG_NAME)
        assert MANAGED_ROLE_NAMES <= set(_roles_by_name(ranger))
        assert DEFAULT_POLICY_NAMES <= set(_policies_in_zone(ranger, CATALOG_NAME))
