# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger credential ownership and recovery integration tests."""

import logging

import jubilant
import pytest
import requests

from integration.helpers import (
    APP_NAME,
    HEADERS,
    get_passwords,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)

OUT_OF_BAND_PASSWORD = "OutOfBand1Password"  # nosec B105
UNKNOWN_PASSWORD = "Unknown1Password"  # nosec B105
RECOVERED_PASSWORD = "Recovered1Password"  # nosec B105
NEW_USERSYNC_PASSWORD = "NewUsersync1Password"  # nosec B105


def authenticates(url: str, username: str, password: str) -> bool:
    """Check whether Ranger accepts a set of credentials.

    Args:
        url: Ranger unit address.
        username: The Ranger internal user.
        password: The password to try.

    Returns:
        Whether Ranger accepted the credentials.
    """
    response = requests.get(
        f"{url}/service/xusers/users/userName/{username}",
        headers=HEADERS,
        auth=(username, password),
        timeout=60,
    )
    return response.status_code == 200


def change_password_out_of_band(url: str, username: str, old: str, new: str) -> None:
    """Change a password behind the charm's back, as the Ranger UI would.

    Args:
        url: Ranger unit address.
        username: The Ranger internal user.
        old: The password Ranger currently holds.
        new: The password to apply.
    """
    user = requests.get(
        f"{url}/service/xusers/users/userName/{username}",
        headers=HEADERS,
        auth=(username, old),
        timeout=60,
    ).json()
    response = requests.post(
        f"{url}/service/users/{user['id']}/passwordchange",
        headers=HEADERS,
        auth=(username, old),
        json={
            "loginId": username,
            "emailAddress": user.get("emailAddress"),
            "oldPassword": old,
            "updPassword": new,
        },
        timeout=60,
    )
    assert response.status_code == 200, response.text


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestCredentials:
    """Integration tests for the charm-owned Ranger credentials."""

    def test_get_password(self, juju: jubilant.Juju):
        """The charm reports a password for every internal user it manages."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        passwords = get_passwords(juju)

        assert sorted(passwords) == ["admin", "keyadmin", "rangertagsync", "rangerusersync"]
        assert authenticates(url, "admin", passwords["admin"])

    def test_rotate_admin_password(self, juju: jubilant.Juju):
        """Rotation replaces the admin password and the application stays healthy."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        old_password = get_passwords(juju)["admin"]

        task = juju.run(f"{APP_NAME}/0", "set-password", {"username": "admin", "rotate": True})
        assert task.results["result"] == "changed"

        new_password = get_passwords(juju)["admin"]
        assert new_password != old_password
        assert authenticates(url, "admin", new_password)
        assert not authenticates(url, "admin", old_password)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=600, idle_period=30)

    def test_set_usersync_password(self, juju: jubilant.Juju):
        """An explicit password is applied to another internal user."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)

        task = juju.run(
            f"{APP_NAME}/0",
            "set-password",
            {"username": "rangerusersync", "password": NEW_USERSYNC_PASSWORD},
        )
        assert task.results["result"] == "changed"

        assert get_passwords(juju)["rangerusersync"] == NEW_USERSYNC_PASSWORD
        assert authenticates(url, "rangerusersync", NEW_USERSYNC_PASSWORD)

    def test_out_of_band_change_blocks(self, juju: jubilant.Juju):
        """A password changed outside the charm leaves the application blocked."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        change_password_out_of_band(
            url, "admin", get_passwords(juju)["admin"], OUT_OF_BAND_PASSWORD
        )

        wait_for_apps(juju, [APP_NAME], status="blocked", timeout=900, idle_period=30)

    def test_override_records_a_known_password(self, juju: jubilant.Juju):
        """Telling the charm the out-of-band password restores the application."""
        task = juju.run(
            f"{APP_NAME}/0",
            "set-password",
            {"username": "admin", "password": OUT_OF_BAND_PASSWORD, "override": True},
        )
        assert task.results["result"] == "recorded"

        wait_for_apps(juju, [APP_NAME], status="active", timeout=900, idle_period=30)
        assert get_passwords(juju)["admin"] == OUT_OF_BAND_PASSWORD

    def test_override_force_resets_an_unknown_password(self, juju: jubilant.Juju):
        """A password nobody knows is recovered through the database."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        change_password_out_of_band(url, "admin", OUT_OF_BAND_PASSWORD, UNKNOWN_PASSWORD)
        wait_for_apps(juju, [APP_NAME], status="blocked", timeout=900, idle_period=30)

        task = juju.run(
            f"{APP_NAME}/0",
            "set-password",
            {"username": "admin", "password": RECOVERED_PASSWORD, "override": True},
        )
        assert task.results["result"] == "force-reset"

        assert authenticates(url, "admin", RECOVERED_PASSWORD)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=900, idle_period=30)
