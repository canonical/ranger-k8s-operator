# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the trino-catalog relation."""

import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
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
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

CATALOG_NAME = "testcat"
DEFAULT_POLICY_NAMES = {
    f"default - {suffix} - {CATALOG_NAME}"
    for suffix in ("ro", "rw", "ddl", "is")
}

POLL_INTERVAL = 15
POLL_TIMEOUT = 300
RECONCILE_CYCLES = 180


async def _get_ranger_client(ops_test: OpsTest) -> RangerClient:
    """Build a RangerClient pointed at the Ranger unit."""
    url = await get_unit_url(ops_test, application=APP_NAME, unit=0, port=6080)
    return RangerClient(url, RANGER_AUTH)


async def _poll_zones(ops_test, zone_name, *, expect_present):
    """Poll Ranger until a zone is present or absent.

    Args:
        ops_test: PyTest OpsTest object.
        zone_name: Name of the security zone to look for.
        expect_present: True to wait for zone to appear, False for disappear.

    Returns:
        The matching zone dict when expect_present=True, None otherwise.

    Raises:
        TimeoutError: if the condition is not met within POLL_TIMEOUT.
    """
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        ranger = await _get_ranger_client(ops_test)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        if expect_present and zone_name in zone_names:
            return ranger.get_security_zone(zone_name)
        if not expect_present and zone_name not in zone_names:
            return None
        await asyncio.sleep(POLL_INTERVAL)
    state = "appear" if expect_present else "disappear"
    raise TimeoutError(
        f"Zone {zone_name!r} did not {state} within {POLL_TIMEOUT}s"
    )


async def _build_catalog_config(ops_test, secret_id):
    """Build the catalog-config YAML string for Trino.

    Dynamically resolves the PostgreSQL JDBC URL and database name from
    the deployed postgresql-k8s application.

    Args:
        ops_test: PyTest OpsTest object.
        secret_id: Juju secret ID containing replica credentials.

    Returns:
        A valid YAML string for the trino-k8s catalog-config option.
    """
    model_name = ops_test.model.name
    pg_url = (
        f"jdbc:postgresql://postgresql-k8s-primary"
        f".{model_name}.svc.cluster.local:5432"
    )

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


async def _create_and_grant_secret(ops_test):
    """Create a Juju user secret with replica credentials and grant it to Trino.

    Returns:
        The secret ID string.
    """
    replicas_content = yaml.dump(
        {
            "ro": {
                "user": "test_ro_user",
                "password": "roPassw0rd",
            },  # nosec B105
            "rw": {
                "user": "test_rw_user",
                "password": "rwPassw0rd",
            },  # nosec B105
        },
        default_flow_style=False,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(replicas_content)
        replicas_path = f.name

    secret_name = "test-pg-credentials"  # nosec B105
    rc, stdout, stderr = await ops_test.juju(
        "add-secret",
        secret_name,
        f"replicas#file={replicas_path}",
    )
    assert rc == 0, f"Failed to create secret: {stderr}"

    secret_id = stdout.strip()
    logger.info("created secret %s with id %s", secret_name, secret_id)

    rc, _, stderr = await ops_test.juju(
        "grant-secret", secret_name, TRINO_NAME
    )
    assert rc == 0, f"Failed to grant secret: {stderr}"

    Path(replicas_path).unlink(missing_ok=True)
    return secret_id


async def _set_catalog_config(ops_test, secret_id):
    """Set catalog-config on the Trino charm."""
    config_yaml = await _build_catalog_config(ops_test, secret_id)
    await ops_test.model.applications[TRINO_NAME].set_config(
        {"catalog-config": config_yaml}
    )
    await ops_test.model.wait_for_idle(
        apps=[TRINO_NAME, APP_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )


async def _clear_catalog_config(ops_test):
    """Remove all catalogs from Trino by clearing catalog-config."""
    empty_config = ""
    await ops_test.model.applications[TRINO_NAME].set_config(
        {"catalog-config": empty_config}
    )
    await ops_test.model.wait_for_idle(
        apps=[TRINO_NAME, APP_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )


@pytest_asyncio.fixture(name="deploy_trino_catalog", scope="module")
async def deploy_trino_catalog_fixture(ops_test: OpsTest, deploy):
    """Deploy Trino and configure the trino-catalog relation with Ranger.

    Reuses the base ``deploy`` fixture (Ranger + PostgreSQL already active).
    Deploys trino-k8s, relates on both ``policy`` and ``trino-catalog``,
    creates a Juju secret with credentials, and sets ``catalog-config``.

    Returns:
        The Juju secret ID used for catalog configuration.
    """
    trino_config = {
        "charm-function": "all",
        "ranger-service-name": TRINO_SERVICE,
    }

    # Deploy Trino only if not already present
    if TRINO_NAME not in ops_test.model.applications:
        await ops_test.model.deploy(
            TRINO_NAME,
            channel="edge",
            config=trino_config,
            trust=True,
        )
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, TRINO_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1500,
        )

    # Integrate policy if not already related
    await ops_test.model.integrate(
        f"{APP_NAME}:policy", f"{TRINO_NAME}:policy"
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TRINO_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    # Integrate trino-catalog
    await ops_test.model.integrate(
        f"{APP_NAME}:trino-catalog", f"{TRINO_NAME}:trino-catalog"
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TRINO_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    # Create secret and configure catalog
    secret_id = await _create_and_grant_secret(ops_test)
    await _set_catalog_config(ops_test, secret_id)

    return secret_id


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy_trino_catalog")
class TestTrinoCatalogRelation:
    """Integration tests for the trino-catalog relation lifecycle."""

    async def test_relation_active(self, ops_test: OpsTest):
        """Validate Ranger and Trino are active after relating on both interfaces."""
        ranger_status = (
            ops_test.model.applications[APP_NAME].units[0].workload_status
        )
        trino_status = (
            ops_test.model.applications[TRINO_NAME].units[0].workload_status
        )
        assert ranger_status == "active"
        assert trino_status == "active"

    async def test_zone_created(self, ops_test: OpsTest):
        """Validate that adding a catalog creates a security zone in Ranger."""
        zone = await _poll_zones(ops_test, CATALOG_NAME, expect_present=True)
        assert zone is not None
        logger.info("zone: %s", zone)

    async def test_zone_default_policies(self, ops_test: OpsTest):
        """Validate that the new zone contains exactly the four default policies."""
        ranger = await _get_ranger_client(ops_test)
        policies = (
            ranger.find_policies(
                {"zoneName": CATALOG_NAME, "serviceName": TRINO_SERVICE}
            )
            or []
        )
        policy_names = {p.name for p in policies}
        logger.info("policies in zone %s: %s", CATALOG_NAME, policy_names)
        assert policy_names == DEFAULT_POLICY_NAMES

    async def test_catalog_removal_cleans_zone(self, ops_test: OpsTest):
        """Validate that removing a catalog from Trino removes the zone from Ranger."""
        await _clear_catalog_config(ops_test)
        await _poll_zones(ops_test, CATALOG_NAME, expect_present=False)

        ranger = await _get_ranger_client(ops_test)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        assert CATALOG_NAME not in zone_names

    async def test_custom_policy_prevents_removal(
        self, ops_test: OpsTest, deploy_trino_catalog
    ):
        """Validate that a zone with custom policies is not removed on catalog removal."""
        secret_id = deploy_trino_catalog

        # Re-add the catalog and wait for the zone to reappear
        await _set_catalog_config(ops_test, secret_id)
        await _poll_zones(ops_test, CATALOG_NAME, expect_present=True)

        # Create a custom policy in the zone via the Ranger REST API
        ranger = await _get_ranger_client(ops_test)
        custom_policy = RangerPolicy(
            {
                "service": TRINO_SERVICE,
                "name": f"custom-test-policy-{CATALOG_NAME}",
                "resources": {
                    "catalog": RangerPolicyResource(
                        {"values": [CATALOG_NAME]}
                    ),
                },
                "policyItems": [
                    RangerPolicyItem(
                        {
                            "users": ["admin"],
                            "accesses": [
                                RangerPolicyItemAccess(
                                    {"type": "select", "isAllowed": True}
                                )
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
        await _clear_catalog_config(ops_test)

        # Wait long enough for at least two reconciliation cycles
        await asyncio.sleep(RECONCILE_CYCLES)

        # The zone should still exist because of the custom policy
        ranger = await _get_ranger_client(ops_test)
        zones = ranger.find_security_zones() or []
        zone_names = {z.name for z in zones}
        assert (
            CATALOG_NAME in zone_names
        ), f"Zone {CATALOG_NAME!r} was removed despite having a custom policy"
