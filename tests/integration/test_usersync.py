# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm usersync integration test."""

import logging
import time
from pathlib import Path

import pytest
import pytest_asyncio
from integration.helpers import (
    APP_NAME,
    LDAP_NAME,
    USERSYNC_NAME,
    get_memberships,
    get_unit_url,
)
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


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestUserSync:
    """Integration test Ranger usersync."""

    async def test_user_sync(
        self, ops_test: OpsTest, charm: str, charm_image: str
    ):
        """Validate users and groups have been synchronized from LDAP."""
        await ops_test.model.deploy(LDAP_NAME, channel="edge")

        await ops_test.model.wait_for_idle(
            apps=[LDAP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )

        ranger_config = {
            "charm-function": "usersync",
            "ranger-usersync-password": "P@ssw0rd1234",
        }

        resources = {
            "ranger-image": charm_image,
        }
        action = (
            await ops_test.model.applications[LDAP_NAME]
            .units[0]
            .run_action("load-test-users")
        )
        await action.wait()

        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=USERSYNC_NAME,
            num_units=1,
            config=ranger_config,
        )

        await ops_test.model.integrate(USERSYNC_NAME, LDAP_NAME)
        time.sleep(100)  # Provide time for user synchronization to occur.
        await ops_test.model.wait_for_idle(
            apps=[USERSYNC_NAME, LDAP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1500,
        )
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        membership = await get_memberships(ops_test, url)

        assert membership == ("finance", 7)
