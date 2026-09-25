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
    get_auth,
    get_memberships,
    get_passwords,
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
            "sync-ldap-url": "ldap://comsys-openldap-k8s:389",
            "sync-ldap-search-base": "dc=canonical,dc=dev,dc=com",
            "sync-ldap-user-search-base": "dc=canonical,dc=dev,dc=com",
            "sync-group-search-base": "dc=canonical,dc=dev,dc=com",
        }
        secret_name = "ranger-usersync-credentials"  # nosec B105
        secret_uri = juju.add_secret(
            secret_name,
            {"rangerusersync": get_passwords(juju)["rangerusersync"]},
        )
        ldap_secret_name = "ranger-usersync-ldap-credentials"  # nosec B105
        ldap_secret_uri = juju.add_secret(
            ldap_secret_name,
            {
                "sync-ldap-bind-dn": "cn=admin,dc=canonical,dc=dev,dc=com",
                "sync-ldap-bind-password": "admin",
            },
        )
        secret_config = {
            "usersync-credentials": secret_uri.unique_identifier,
            "ldap-credentials": ldap_secret_uri.unique_identifier,
        }
        resources = {
            "ranger-image": charm_image,
        }
        juju.run(f"{LDAP_NAME}/0", "load-test-users")

        admin_url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        ranger_config["policy-mgr-url"] = admin_url

        juju.deploy(
            charm,
            app=USERSYNC_NAME,
            resources=resources,
            num_units=1,
            config=ranger_config,
        )

        juju.grant_secret(secret_name, USERSYNC_NAME)
        juju.grant_secret(ldap_secret_name, USERSYNC_NAME)
        juju.config(USERSYNC_NAME, secret_config)
        juju.integrate(USERSYNC_NAME, LDAP_NAME)
        wait_for_apps(juju, [USERSYNC_NAME, LDAP_NAME], status="active", timeout=1500)

        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        auth = get_auth(juju)
        membership = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            membership = get_memberships(url, auth)
            if membership is not None:
                break
            time.sleep(10)

        assert membership == ("finance", 7)

    def test_usersync_password_rotation(self, juju: jubilant.Juju):
        """Rotating rangerusersync blocks usersync until its secret is updated."""
        task = juju.run(f"{APP_NAME}/0", "set-password", {"username": "rangerusersync"})
        assert task.results["result"] == "changed"

        wait_for_apps(juju, [USERSYNC_NAME], status="blocked", timeout=900, idle_period=30)
        status = juju.status()
        assert status.apps[APP_NAME].app_status.current == "active"

        juju.update_secret(
            "ranger-usersync-credentials",
            {"rangerusersync": get_passwords(juju)["rangerusersync"]},
        )
        wait_for_apps(juju, [USERSYNC_NAME], status="active", timeout=900, idle_period=30)
