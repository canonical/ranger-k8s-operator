#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino policy integration tests."""

import logging
import time
import json
import pytest
import pytest_asyncio
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import (
    GROUP_MANAGEMENT,
    METADATA,
    POSTGRES_NAME,
    APP_NAME,
    TRINO_NAME,
    get_unit_url,
    HEADERS,
    RANGER_AUTH
)
from apache_ranger.client import ranger_client
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy", scope="module")
async def deploy_ranger(ops_test: OpsTest):
    """Add Ranger relation and apply group configuration."""
    charm = await ops_test.build_charm(".")
    resources = {
        "trino-image": METADATA["resources"]["ranger-image"]["upstream-source"]
    }

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
        timeout=1200,
    )
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME, APP_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    await ops_test.model.integrate(RANGER_NAME, POSTGRES_NAME)
    await ops_test.model.set_config({"update-status-hook-interval": "1m"})
    await ops_test.model.wait_for_idle(
        apps=[POSTGRES_NAME, RANGER_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    trino_config = {
        "charm-function": "all",
        "ranger-service-name": TRINO_SERVICE,
    }
    
    await ops_test.model.deploy(
        TRINO_NAME, channel="beta", config=trino_config
    )

    await ops_test.model.wait_for_idle(
        apps=[TRINO_NAME, APP_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )

    logging.info("integrating trino and ranger")
    await ops_test.model.integrate(RANGER_NAME, TRINO_NAME)
    await ops_test.model.wait_for_idle(
        apps=[TRINO_NAME, RANGER_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1200,
    )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestPolicy:
    """Integration tests for charm."""
    async def check_service_created(self, ops_test: OpsTest):
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        ranger = ranger_client.RangerClient(RANGER_URL, RANGER_AUTH)

        new_service = ranger.get_service(TRINO_SERVICE)
        assert new_service == TRINO_SERVICE

    async def check_group_membership(self, ops_test: OpsTest):
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        url = f"{RANGER_URL}/service/xusers/groupusers"
        response = requests.get(url, headers=HEADERS, auth=RANGER_AUTH)
        membership = json.loads(response.text)
        assert membership == []
