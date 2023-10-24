#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""
import json
import logging

import pytest
import requests
from apache_ranger.client import ranger_client
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import APP_NAME, HEADERS, RANGER_AUTH, TRINO_SERVICE, get_unit_url
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
        url = f"{url}/service/xusers/groupusers"
        response = requests.get(
            url, headers=HEADERS, auth=RANGER_AUTH, timeout=20
        )
        data = json.loads(response.text)
        group = data["vXGroupUsers"][0].get("name")
        user_id = data["vXGroupUsers"][0].get("userId")
        membership = (group, user_id)

        assert membership == ("commercial-systems", 8)
