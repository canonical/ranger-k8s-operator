#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration tests."""

import logging
import subprocess  # nosec B404
import time

import jubilant
import pytest
import requests

from integration.conftest import deploy  # noqa: F401, pylint: disable=W0611
from integration.helpers import (
    APP_NAME,
    POSTGRES_NAME,
    TRAEFIK_NAME,
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
        """Integrate Ranger with Traefik ingress and verify the policy URL is updated."""
        juju.deploy(
            TRAEFIK_NAME,
            config={"routing_mode": "subdomain", "external_hostname": "example.com"},
            trust=True,
        )
        wait_for_apps(juju, [TRAEFIK_NAME], status="active", timeout=1500)

        juju.integrate(APP_NAME, TRAEFIK_NAME)
        wait_for_apps(juju, [TRAEFIK_NAME, APP_NAME], status="active", timeout=1000)

        internal_url = f"ranger-k8s.{juju.model}.svc.cluster.local:6080"
        result_url = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = juju.status()
            app_data = status.apps[APP_NAME].units.get(f"{APP_NAME}/0")
            if app_data:
                action = juju.run(f"{APP_NAME}/0", "get-relation-data")
                result_url = (action.results or {}).get("policy_manager_url")
            if result_url and internal_url not in result_url:
                break
            time.sleep(5)

        assert result_url is not None
        assert internal_url not in result_url
        assert "example.com" in result_url

        juju.remove_relation(APP_NAME, TRAEFIK_NAME)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=600)
        juju.remove_application(TRAEFIK_NAME, destroy_storage=True)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=600)

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
