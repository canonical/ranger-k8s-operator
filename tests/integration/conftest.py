# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    APP_NAME,
    GROUP_MANAGEMENT,
    METADATA,
    NGINX_NAME,
    POSTGRES_NAME,
    TRINO_NAME,
    TRINO_SERVICE,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest):
    """Deploy the app."""
    charm = await ops_test.build_charm(".")
    resources = {
        "ranger-image": METADATA["resources"]["ranger-image"][
            "upstream-source"
        ]
    }
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )

    ranger_config = {"user-group-configuration": GROUP_MANAGEMENT}
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        num_units=1,
        config=ranger_config,
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=1000,
    )

    await ops_test.model.integrate(APP_NAME, POSTGRES_NAME)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, POSTGRES_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    trino_config = {
        "charm-function": "all",
        "ranger-service-name": TRINO_SERVICE,
    }

    await ops_test.model.deploy(
        TRINO_NAME,
        channel="beta",
        config=trino_config,
        trust=True,
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TRINO_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )
    await ops_test.model.integrate(APP_NAME, TRINO_NAME)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TRINO_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )
    await ops_test.model.deploy(NGINX_NAME, trust=True)
    await ops_test.model.wait_for_idle(
        apps=[NGINX_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    await ops_test.model.integrate(APP_NAME, NGINX_NAME)
    await ops_test.model.wait_for_idle(
        apps=[NGINX_NAME, APP_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )
    assert (
        ops_test.model.applications[APP_NAME].units[0].workload_status
        == "active"
    )
