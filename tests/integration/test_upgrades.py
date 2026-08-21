# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger charm upgrades integration tests."""

import logging

import jubilant
import pytest
import requests

from integration.helpers import (
    APP_NAME,
    POSTGRES_NAME,
    SECURE_PWD,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)


@pytest.fixture(name="deploy", scope="module")
def deploy(juju: jubilant.Juju):
    """Deploy the app."""
    juju.deploy(POSTGRES_NAME, channel="14", trust=True)

    juju.deploy(APP_NAME, channel="edge")

    wait_for_apps(juju, [POSTGRES_NAME], status="active", timeout=1500)
    wait_for_apps(juju, [APP_NAME], status="blocked", timeout=1000)

    juju.integrate(APP_NAME, POSTGRES_NAME)
    wait_for_apps(
        juju,
        [APP_NAME, POSTGRES_NAME],
        status="active",
        timeout=1500,
        idle_period=30,
    )


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestUpgrade:
    """Integration test for Ranger charm upgrade from previous release."""

    def test_upgrade(self, juju: jubilant.Juju, charm: str, charm_image: str):
        """Builds the current charm and refreshes the current deployment."""
        resources = {
            "ranger-image": charm_image,
        }

        secret_name = "ranger-upgrade-system-users"  # nosec B105
        secret_uri = juju.add_secret(
            secret_name,
            {"admin": SECURE_PWD, "rangerusersync": "RangerUsersync1"},
        )
        juju.grant_secret(secret_name, APP_NAME)

        juju.refresh(APP_NAME, path=str(charm), resources=resources)
        juju.config(APP_NAME, {"system-users": secret_uri.unique_identifier})
        wait_for_apps(juju, [APP_NAME], status="active", timeout=600, idle_period=30)

        status = juju.status()
        unit = status.apps[APP_NAME].units[f"{APP_NAME}/0"]
        assert unit.workload_status.current == "active"

    def test_ui_relation(self, juju: jubilant.Juju):
        """Perform GET request on the Ranger UI host."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        logger.info("curling app address: %s", url)

        response = requests.get(url, timeout=300)
        assert response.status_code == 200

    def test_system_users_secret_applied(self, juju: jubilant.Juju):
        """Validate the application remains active after the secret-backed upgrade."""
        status = juju.status()
        assert status.apps[APP_NAME].units[f"{APP_NAME}/0"].workload_status.current == "active"
