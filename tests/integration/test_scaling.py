# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm scaling integration test."""

import logging

import requests
import pytest
from helpers import APP_NAME, get_application_url, get_memberships, scale
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy")
class TestScaling:
    """Integration tests for scaling Ranger charm."""

    async def test_scaling_up(self, ops_test: OpsTest):
        """Scale Ranger charm up to 2 units."""
        await scale(ops_test, app=APP_NAME, units=2)
        assert len(ops_test.model.applications[APP_NAME].units) == 2

        url = await get_application_url(
            ops_test, application=APP_NAME, port=6080
        )
        logger.info("curling app address: %s", url)
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    async def test_scaling_down(self, ops_test: OpsTest):
        """Scale Ranger charm down to 1 unit."""
        await scale(ops_test, app=APP_NAME, units=1)
        assert len(ops_test.model.applications[APP_NAME].units) == 1

        url = await get_application_url(
            ops_test, application=APP_NAME, port=6080
        )
        logger.info("curling app address: %s", url)
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200
