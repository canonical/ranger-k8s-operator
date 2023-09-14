#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging
import socket
import unittest.mock

import pytest
import requests
from conftest import deploy  # noqa: F401, pylint: disable=W0611
from helpers import APP_NAME, gen_patch_getaddrinfo, get_unit_url
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

    async def test_ingress(self, ops_test: OpsTest):
        """Set external-hostname and test connectivity through ingress."""
        new_hostname = "ranger-admin"
        application = ops_test.model.applications[APP_NAME]
        await application.set_config({"external-hostname": new_hostname})
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, "nginx-ingress-integrator"],
            status="active",
            raise_on_blocked=False,
            timeout=600,
        )
        with unittest.mock.patch.multiple(
            socket,
            getaddrinfo=gen_patch_getaddrinfo(new_hostname, "127.0.0.1"),
        ):
            response = requests.get(
                f"https://{new_hostname}", timeout=300, verify=False
            )  # nosec
            assert response.status_code == 200
