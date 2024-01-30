#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""
import logging

import pytest
import requests
from apache_ranger.client import ranger_client
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import (
    APP_NAME,
    METADATA,
    POSTGRES_NAME,
    RANGER_AUTH,
    TRINO_SERVICE,
    get_memberships,
    get_unit_url,
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

    async def test_service_created(self, ops_test: OpsTest):
        """Validate the service `trino-service` has been created."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        ranger = ranger_client.RangerClient(url, RANGER_AUTH)

        new_service = ranger.get_service(TRINO_SERVICE)
        logger.info(f"service: {new_service}")
        name = new_service.get("name")
        service_id = new_service.get("id")
        assert name == TRINO_SERVICE and service_id == 1

    async def test_group_membership(self, ops_test: OpsTest):
        """Validate `user-group-configuration` value has been synchronized."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        membership = await get_memberships(ops_test, url)

        assert membership == ("commercial-systems", 8)

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

        membership = await get_memberships(ops_test, url)
        logger.info(f"Ranger memberships: {membership}")
        assert membership == ("commercial-systems", 8)
