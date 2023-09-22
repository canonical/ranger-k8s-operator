#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging

import pytest
import requests
from apache_ranger.client import ranger_client
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import APP_NAME, RANGER_AUTH, RANGER_URL, get_unit_url
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for charm."""

    async def test_ui(self, ops_test: OpsTest):
        """Perform GET request on the Ranger UI host."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    async def test_service_created(self, ops_test: OpsTest):
        """Check Trino service has been created in Ranger."""
        response = requests.get(RANGER_URL, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

        ranger = ranger_client.RangerClient(RANGER_URL, RANGER_AUTH)
        retrieved_service = ranger.get_service("trino")
        assert retrieved_service is not None
