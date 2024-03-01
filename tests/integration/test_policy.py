# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm scaling integration test."""

import logging

import pytest
from helpers import TRINO_SERVICE, APP_NAME, TRINO_NAME
from pytest_operator.plugin import OpsTest
from apache_ranger.client import ranger_client
from helpers import get_unit_url, RANGER_AUTH

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestPolicyRelation:
    """Integration tests for establishing a policy relation."""

    async def test_create_service(self, ops_test: OpsTest):
        """Validate the service `trino-service` has been created."""
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

        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        ranger = ranger_client.RangerClient(url, RANGER_AUTH)

        new_service = ranger.get_service(TRINO_SERVICE)
        logger.info(f"service: {new_service}")
        name = new_service.get("name")
        service_id = new_service.get("id")
        assert name == TRINO_SERVICE and service_id == 1
