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
            "sync-ldap-url": "ldap://comsys-openldap-k8s:389",
            "sync-ldap-search-base": "dc=canonical,dc=dev,dc=com",
            "sync-ldap-user-search-base": "dc=canonical,dc=dev,dc=com",
            "sync-group-search-base": "dc=canonical,dc=dev,dc=com",
        }
        secret_name = "ranger-usersync-system-users"  # nosec B105
        secret_uri = juju.add_secret(
            secret_name,
            {"admin": "RangerAdmin1", "rangerusersync": "RangerUsersync1"},
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
            "system-users": secret_uri.unique_identifier,
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
        membership = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            membership = get_memberships(url)
            if membership is not None:
                break
            time.sleep(10)

        assert membership == ("finance", 7)
