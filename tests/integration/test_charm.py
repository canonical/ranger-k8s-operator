#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging
import subprocess  # nosec B404

import jubilant
import pytest
import requests

from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    APP_NAME,
    NGINX_NAME,
    POSTGRES_NAME,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestDeployment:
    """Integration tests for Ranger charm."""

    def test_ui(self, juju: jubilant.Juju):
        """Perform GET request on the Ranger UI host."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    def test_ingress(self, juju: jubilant.Juju):
        """Integrate Ranger with Ingress."""
        juju.deploy(NGINX_NAME, trust=True)
        wait_for_apps(juju, [NGINX_NAME], status="waiting", timeout=1500)

        juju.integrate(APP_NAME, NGINX_NAME)
        wait_for_apps(juju, [NGINX_NAME, APP_NAME], status="active", timeout=1000)

        status = juju.status()
        nginx_unit = status.apps[NGINX_NAME].units[f"{NGINX_NAME}/0"]
        assert nginx_unit.workload_status.current == "active"

    def test_simulate_crash(self, juju: jubilant.Juju):
        """Simulate the crash of the Ranger charm by force-deleting its pod.

        Args:
            juju: Jubilant Juju object.
        """
        subprocess.run(  # nosec B603 B607
            [
                "kubectl",
                "delete",
                "pod",
                f"{APP_NAME}-0",
                "-n",
                juju.model,
                "--grace-period=0",
                "--force",
            ],
            check=True,
        )
        wait_for_apps(
            juju,
            [APP_NAME, POSTGRES_NAME],
            status="active",
            timeout=1500,
            idle_period=30,
        )

        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200
