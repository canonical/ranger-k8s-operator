#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino policy integration tests."""

import json
import logging

import pytest
import pytest_asyncio
import requests
from apache_ranger.client import ranger_client
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import (
    GROUP_MANAGEMENT,
    HEADERS,
    METADATA,
    POSTGRES_POLICY,
    RANGER_AUTH,
    RANGER_POLICY,
    TRINO_NAME,
    TRINO_SERVICE,
    get_unit_url,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy_ranger(ops_test: OpsTest):
    """Add Ranger relation and apply group configuration."""
    charm = await ops_test.build_charm(".")
    resources = {
        "ranger-image": METADATA["resources"]["ranger-image"][
            "upstream-source"
        ]
    }

    await ops_test.model.deploy(POSTGRES_POLICY, channel="14", trust=True)

    ranger_config = {"user-group-configuration": GROUP_MANAGEMENT}
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=RANGER_POLICY,
        num_units=1,
        config=ranger_config,
    )

    await ops_test.model.wait_for_idle(
        apps=[RANGER_POLICY],
        status="blocked",
        raise_on_blocked=False,
        timeout=1200,
    )
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_POLICY],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    await ops_test.model.integrate(RANGER_POLICY, POSTGRES_POLICY)
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_POLICY, RANGER_POLICY],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
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
        apps=[TRINO_NAME, RANGER_POLICY],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    logging.info("integrating trino and ranger")
    await ops_test.model.integrate(RANGER_POLICY, TRINO_NAME)
    await ops_test.model.wait_for_idle(
        apps=[TRINO_NAME, RANGER_POLICY],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestPolicy:
    """Integration tests for charm."""

    async def test_service_created(self, ops_test: OpsTest):
        """Validate the service `trino-service` has been created."""
        url = await get_unit_url(
            ops_test, application=RANGER_POLICY, unit=0, port=6080
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
            ops_test, application=RANGER_POLICY, unit=0, port=6080
        )
        url = f"{url}/service/xusers/groupusers"
        response = requests.get(url, headers=HEADERS, auth=RANGER_AUTH)
        data = json.loads(response.text)
        group = data["vXGroupUsers"][0].get("name")
        user_id = data["vXGroupUsers"][0].get("userId")
        membership = (group, user_id)

        assert membership == ("commercial-systems", 8)
