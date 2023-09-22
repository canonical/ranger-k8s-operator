# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging

import pytest
import pytest_asyncio
from helpers import (
    APP_NAME,
    METADATA,
    NGINX_NAME,
    POSTGRES_NAME,
    TRINO_NAME,
    perform_ranger_integrations,
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
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        num_units=1,
    )
    await ops_test.model.deploy(NGINX_NAME, trust=True)
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=TRINO_NAME,
        num_units=1,
        config={"charm-function": "all"},
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[POSTGRES_NAME, TRINO_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )
        await perform_ranger_integrations(ops_test, APP_NAME)

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
