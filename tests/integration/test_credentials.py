# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger credential ownership and recovery integration tests."""

import logging
import time

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


def authenticate(url: str, username: str, password: str):
    """Ask Ranger to authenticate a set of credentials.

    Args:
        url: Ranger unit address.
        username: The Ranger internal user.
        password: The password to try.

    Returns:
        The status code Ranger returned, or None when it was unreachable.
    """
    try:
        response = requests.get(
            f"{url}/service/xusers/users/userName/{username}",
            headers=HEADERS,
            auth=(username, password),
            timeout=60,
        )
    except requests.exceptions.RequestException:
        return None
    return response.status_code


def wait_for_authentication(url: str, username: str, password: str, accepted: bool = True) -> None:
    """Wait until Ranger reaches a verdict on a set of credentials.

    Ranger restarts while the charm reconciles a password change, so a single
    request can hit a window where nothing is listening.

    Args:
        url: Ranger unit address.
        username: The Ranger internal user.
        password: The password to try.
        accepted: The verdict to wait for.

    Raises:
        TimeoutError: If Ranger does not reach that verdict in time.
    """
    expected = 200 if accepted else 401
    deadline = time.monotonic() + 600
    status = None
    while time.monotonic() < deadline:
        status = authenticate(url, username, password)
        if status == expected:
            return
        time.sleep(15)
    raise TimeoutError(
        f"Ranger did not return {expected} for {username}; last response was {status}."
    )


def wait_for_ranger(url: str) -> None:
    """Wait until Ranger answers HTTP at all.

    Args:
        url: Ranger unit address.

    Raises:
        TimeoutError: If Ranger does not answer in time.
    """
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            requests.get(url, timeout=60)
        except requests.exceptions.RequestException:
            time.sleep(15)
            continue
        return
    raise TimeoutError(f"Ranger at {url} did not answer.")


def change_password_out_of_band(url: str, username: str, old: str, new: str) -> None:
    """Change a password behind the charm's back, as the Ranger UI would.

    Args:
        url: Ranger unit address.
        username: The Ranger internal user.
        old: The password Ranger currently holds.
        new: The password to apply.
    """
    wait_for_authentication(url, username, old)
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
        wait_for_authentication(url, "admin", passwords["admin"])

    def test_rotate_admin_password(self, juju: jubilant.Juju):
        """Rotation replaces the admin password and the application stays healthy."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        old_password = get_passwords(juju)["admin"]

        task = juju.run(f"{APP_NAME}/0", "set-password", {"username": "admin", "rotate": True})
        assert task.results["result"] == "changed"

        new_password = get_passwords(juju)["admin"]
        assert new_password != old_password
        wait_for_authentication(url, "admin", new_password)
        wait_for_authentication(url, "admin", old_password, accepted=False)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=900, idle_period=30)

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
        wait_for_authentication(url, "rangerusersync", NEW_USERSYNC_PASSWORD)

    def test_out_of_band_change_blocks(self, juju: jubilant.Juju):
        """A password changed outside the charm leaves the application blocked."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        change_password_out_of_band(
            url, "admin", get_passwords(juju)["admin"], OUT_OF_BAND_PASSWORD
        )

        wait_for_apps(juju, [APP_NAME], status="blocked", timeout=900, idle_period=30)

    def test_override_records_a_known_password(self, juju: jubilant.Juju):
        """Telling the charm the out-of-band password restores the application."""
        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        wait_for_authentication(url, "admin", OUT_OF_BAND_PASSWORD)

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
        wait_for_ranger(url)

        task = juju.run(
            f"{APP_NAME}/0",
            "set-password",
            {"username": "admin", "password": RECOVERED_PASSWORD, "override": True},
        )
        assert task.results["result"] == "force-reset"

        wait_for_authentication(url, "admin", RECOVERED_PASSWORD)
        wait_for_apps(juju, [APP_NAME], status="active", timeout=900, idle_period=30)
