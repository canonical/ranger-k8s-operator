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
    get_passwords,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)

# The password the previously published revision seeds Ranger with.
PREVIOUS_ADMIN_PASSWORD = "rangerR0cks!"  # nosec B105


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

        juju.refresh(APP_NAME, path=str(charm), resources=resources)
        # The refreshed charm generates its own passwords, which Ranger does not yet
        # know, so the application blocks until its record is reconciled.
        wait_for_apps(juju, [APP_NAME], status="blocked", timeout=600, idle_period=30)

        task = juju.run(
            f"{APP_NAME}/0",
            "set-password",
            {"username": "admin", "password": PREVIOUS_ADMIN_PASSWORD, "override": True},
        )
        assert task.results["result"] == "recorded"

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

    def test_recorded_password_authenticates(self, juju: jubilant.Juju):
        """Validate the reconciled password is the one the charm now reports."""
        assert get_passwords(juju)["admin"] == PREVIOUS_ADMIN_PASSWORD
