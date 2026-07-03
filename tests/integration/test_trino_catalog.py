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
DEFAULT_POLICY_NAMES = {
    f"default - {suffix} - {CATALOG_NAME}" for suffix in ("ro", "rw", "ddl", "is")
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


def _poll_zones(juju, zone_name, *, expect_present):
    """Poll Ranger until a zone is present or absent.

    Args:
        juju: Jubilant Juju object.
        zone_name: Name of the security zone to look for.
        expect_present: True to wait for zone to appear, False for disappear.

    Returns:
        The matching zone dict when expect_present=True, None otherwise.

    Raises:
        TimeoutError: if the condition is not met within POLL_TIMEOUT.
    """
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        ranger = _get_ranger_client(juju)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        if expect_present and zone_name in zone_names:
            return ranger.get_security_zone(zone_name)
        if not expect_present and zone_name not in zone_names:
            return None
        time.sleep(POLL_INTERVAL)
    state = "appear" if expect_present else "disappear"
    raise TimeoutError(f"Zone {zone_name!r} did not {state} within {POLL_TIMEOUT}s")


def _build_catalog_config(juju, secret_id):
    """Build the catalog-config YAML string for Trino.

    Dynamically resolves the PostgreSQL JDBC URL and database name from
    the deployed postgresql-k8s application.

    Args:
        juju: Jubilant Juju object.
        secret_id: Juju secret ID containing replica credentials.

    Returns:
        A valid YAML string for the trino-k8s catalog-config option.
    """
    model_name = juju.status().model.name
    pg_url = f"jdbc:postgresql://postgresql-k8s-primary.{model_name}.svc.cluster.local:5432"

    db_name = CATALOG_NAME

    config = {
        "catalogs": {
            CATALOG_NAME: {
                "backend": "test-backend",
                "database": db_name,
                "secret-id": secret_id,
            },
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


def _set_catalog_config(juju, secret_id):
    """Set catalog-config on the Trino charm."""
    config_yaml = _build_catalog_config(juju, secret_id)
    juju.config(TRINO_NAME, {"catalog-config": config_yaml})
    wait_for_apps(juju, [TRINO_NAME, APP_NAME], status="active", timeout=1500)


def _clear_catalog_config(juju):
    """Remove all catalogs from Trino by clearing catalog-config."""
    empty_config = ""
    juju.config(TRINO_NAME, {"catalog-config": empty_config})
    wait_for_apps(juju, [TRINO_NAME, APP_NAME], status="active", timeout=1500)


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
        zone = _poll_zones(juju, CATALOG_NAME, expect_present=True)
        assert zone is not None
        logger.info("zone: %s", zone)

    def test_zone_default_policies(self, juju: jubilant.Juju):
        """Validate that the new zone contains exactly the four default policies."""
        ranger = _get_ranger_client(juju)
        policies = (
            ranger.find_policies({"zoneName": CATALOG_NAME, "serviceName": TRINO_SERVICE}) or []
        )
        policy_names = {p.name for p in policies}
        logger.info("policies in zone %s: %s", CATALOG_NAME, policy_names)
        assert policy_names == DEFAULT_POLICY_NAMES

    def test_catalog_removal_cleans_zone(self, juju: jubilant.Juju):
        """Validate that removing a catalog from Trino removes the zone from Ranger."""
        _clear_catalog_config(juju)
        _poll_zones(juju, CATALOG_NAME, expect_present=False)

        ranger = _get_ranger_client(juju)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        assert CATALOG_NAME not in zone_names

    def test_custom_policy_prevents_removal(self, juju: jubilant.Juju, deploy_trino_catalog):
        """Validate that a zone with custom policies is not removed on catalog removal."""
        secret_id = deploy_trino_catalog

        # Re-add the catalog and wait for the zone to reappear
        _set_catalog_config(juju, secret_id)
        _poll_zones(juju, CATALOG_NAME, expect_present=True)

        # Create a custom policy in the zone via the Ranger REST API
        ranger = _get_ranger_client(juju)
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
        ranger.create_policy(custom_policy)
        logger.info("created custom policy in zone %s", CATALOG_NAME)

        # Remove the catalog from Trino
        _clear_catalog_config(juju)

        # Wait long enough for at least two reconciliation cycles
        time.sleep(RECONCILE_CYCLES)

        # The zone should still exist because of the custom policy
        ranger = _get_ranger_client(juju)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        assert CATALOG_NAME in zone_names, (
            f"Zone {CATALOG_NAME!r} was removed despite having a custom policy"
        )
