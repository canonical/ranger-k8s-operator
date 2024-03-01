#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""
import logging

import pytest
import requests
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import (
    APP_NAME,
    NGINX_NAME,
    METADATA,
    POSTGRES_NAME,
    get_unit_url,
    get_application_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Ranger charm."""

    async def test_ui(self, ops_test: OpsTest):
        """Perform GET request on the Ranger UI host."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    async def test_ingress(self, ops_test: OpsTest):
        """Integrate Ranger with Ingress."""
        await ops_test.model.deploy(NGINX_NAME, trust=True)
        await ops_test.model.wait_for_idle(
            apps=[NGINX_NAME],
            status="waiting",
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
            ops_test.model.applications[NGINX_NAME].units[0].workload_status
            == "active"
        )

    async def test_simulate_crash(self, ops_test: OpsTest):
        """Simulate the crash of the Ranger charm.

        Args:
            ops_test: PyTest object.
        """
        # Destroy charm
        await ops_test.model.applications[APP_NAME].destroy()
        await ops_test.model.block_until(
            lambda: APP_NAME not in ops_test.model.applications
        )

        # Deploy charm again
        charm = await ops_test.build_charm(".")
        resources = {
            "ranger-image": METADATA["resources"]["ranger-image"][
                "upstream-source"
            ]
        }
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=APP_NAME,
            num_units=1,
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
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200
