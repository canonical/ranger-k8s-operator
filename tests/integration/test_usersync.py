# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm usersync integration test."""

import logging
import time

import jubilant
import pytest

from integration.helpers import (
    APP_NAME,
    LDAP_NAME,
    USERSYNC_NAME,
    get_memberships,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestUserSync:
    """Integration test Ranger usersync."""

    def test_user_sync(self, juju: jubilant.Juju, charm: str, charm_image: str):
        """Validate users and groups have been synchronized from LDAP."""
        juju.deploy(LDAP_NAME, channel="edge")
        wait_for_apps(juju, [LDAP_NAME], status="active", timeout=600)

        ranger_config = {
            "charm-function": "usersync",
            "ranger-usersync-password": "P@ssw0rd1234",
        }

        resources = {
            "ranger-image": charm_image,
        }
        juju.run(f"{LDAP_NAME}/0", "load-test-users")

        juju.deploy(
            charm,
            app=USERSYNC_NAME,
            resources=resources,
            num_units=1,
            config=ranger_config,
        )

        juju.integrate(USERSYNC_NAME, LDAP_NAME)
        wait_for_apps(juju, [USERSYNC_NAME, LDAP_NAME], status="active", timeout=1500)
        time.sleep(100)  # Provide time for user synchronization to occur.

        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        membership = get_memberships(url)

        assert membership == ("finance", 7)
