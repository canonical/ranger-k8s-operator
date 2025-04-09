# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging
from pathlib import Path

import pytest
import pytest_asyncio
from integration.helpers import APP_NAME, POSTGRES_NAME
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="charm_image")
def charm_image_fixture(request: FixtureRequest) -> str:
    """The OCI image for charm."""
    charm_image = request.config.getoption("--ranger-image")
    assert (
        charm_image
    ), "--ranger-image argument is required which should contain the name of the OCI image."
    return charm_image


@pytest_asyncio.fixture(scope="module", name="charm")
async def charm_fixture(
    request: FixtureRequest, ops_test: OpsTest
) -> str | Path:
    """Fetch the path to charm."""
    charms = request.config.getoption("--charm-file")
    if not charms:
        charm = await ops_test.build_charm(".")
        assert charm, "Charm not built"
        return charm
    return charms[0]


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy(ops_test: OpsTest, charm: str, charm_image: str):
    """Deploy the app."""
    resources = {
        "ranger-image": charm_image,
    }
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )

    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        num_units=1,
        config={"ranger-usersync-password": "P@ssw0rd1234"},
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        raise_on_blocked=False,
        timeout=1000,
    )

    await ops_test.model.integrate(APP_NAME, POSTGRES_NAME)

    await ops_test.model.set_config({"update-status-hook-interval": "1m"})
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, POSTGRES_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )
    assert (
        ops_test.model.applications[APP_NAME].units[0].workload_status
        == "active"
    )
