#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import jubilant
import requests
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
POSTGRES_NAME = "postgresql-k8s"
APP_NAME = "ranger-k8s"
USERSYNC_NAME = "ranger-usersync-k8s"
TRAEFIK_NAME = "traefik-k8s"
TRINO_SERVICE = "trino-service"
TRINO_NAME = "trino-k8s"
RANGER_URL = "http://localhost:6080"
RANGER_AUTH = ("admin", "rangerR0cks!")
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
SECURE_PWD = "ubuntuR0cks!"  # nosec
LDAP_NAME = "comsys-openldap-k8s"

LXD_MODEL_CONFIG = {
    "logging-config": "<root>=INFO;unit=DEBUG",
    "update-status-hook-interval": "5m",
    "cloudinit-userdata": """postruncmd:
        - [ 'sysctl', '-w', 'vm.max_map_count=262144' ]
        - [ 'sysctl', '-w', 'fs.file-max=1048576' ]
        - [ 'sysctl', '-w', 'vm.swappiness=0' ]
        - [ 'sysctl', '-w', 'net.ipv4.tcp_retries2=5' ]
    """,
}


@contextmanager
def fast_forward_ctx(juju: jubilant.Juju, interval: str) -> Generator[None, None, None]:
    """Temporarily set the update-status hook interval.

    Simulates the OpsTest `fast_forward` context manager: the previous
    interval is restored when the context exits.

    Args:
        juju: Jubilant Juju object.
        interval: Update-status hook interval to apply, for example `"10s"`.

    Yields:
        None.
    """
    old_interval = juju.model_config()["update-status-hook-interval"]
    try:
        juju.model_config({"update-status-hook-interval": interval})
        yield
    finally:
        juju.model_config({"update-status-hook-interval": old_interval})


def _apps_ready(model_status, apps, status, exact_units) -> bool:
    """Return True when every app (and its units) has reached the target status.

    Args:
        model_status: A Jubilant Status object.
        apps: Applications to check.
        status: Target workload/application status, for example `"active"`.
        exact_units: Mapping of app name to the exact unit count required.

    Returns:
        True if all applications and units match the target status.
    """
    for app in apps:
        if app not in model_status.apps:
            return False
        app_status = model_status.apps[app]
        if app_status.app_status.current != status:
            return False
        if app in exact_units and len(app_status.units) != exact_units[app]:
            return False
        if any(unit.workload_status.current != status for unit in app_status.units.values()):
            return False
    return True


def _apps_in_error(model_status, apps, status, raise_on_blocked) -> bool:
    """Return True if any app or unit has entered an error (or blocked) state.

    Args:
        model_status: A Jubilant Status object.
        apps: Applications to check.
        status: Target status being waited for.
        raise_on_blocked: Whether a "blocked" status should be treated as an error.

    Returns:
        True if an app or unit is in error (or unexpectedly blocked).
    """
    for app in apps:
        app_status = model_status.apps.get(app)
        if app_status is None:
            continue
        if app_status.app_status.current == "error":
            return True
        if raise_on_blocked and app_status.app_status.current == "blocked" and status != "blocked":
            return True
        for unit_status in app_status.units.values():
            if unit_status.workload_status.current == "error":
                return True
            if (
                raise_on_blocked
                and unit_status.workload_status.current == "blocked"
                and status != "blocked"
            ):
                return True
    return False


def wait_for_apps(
    juju: jubilant.Juju,
    apps,
    *,
    status,
    timeout,
    raise_on_blocked=False,
    wait_for_exact_units=None,
    idle_period=None,
    delay=2.0,
    fast_forward="10s",
):
    """Wait for applications to reach a target status.

    Approximates OpsTest's `wait_for_idle` using Jubilant's `Juju.wait`.

    Args:
        juju: Jubilant Juju object.
        apps: Applications to wait for.
        status: Target workload/application status, for example `"active"`.
        timeout: Overall timeout in seconds.
        raise_on_blocked: Raise if an app or unit becomes blocked.
        wait_for_exact_units: Exact unit count required, as an int (applied to all
            apps) or a mapping of app name to count.
        idle_period: Seconds of stability required; converted to a number of
            consecutive successful status checks.
        delay: Delay in seconds between status checks.
        fast_forward: Update-status hook interval to apply while waiting. Set to a
            falsy value to disable fast-forwarding.

    Returns:
        The final Jubilant Status object.
    """
    exact_units = {}
    if isinstance(wait_for_exact_units, dict):
        exact_units = wait_for_exact_units
    elif isinstance(wait_for_exact_units, int):
        exact_units = dict.fromkeys(apps, wait_for_exact_units)

    successes = max(3, int(idle_period / delay)) if idle_period else 3

    def ready(model_status):
        return _apps_ready(model_status, apps, status, exact_units)

    def error(model_status):
        return _apps_in_error(model_status, apps, status, raise_on_blocked)

    if not fast_forward:
        return juju.wait(ready, error=error, delay=delay, timeout=timeout, successes=successes)

    with fast_forward_ctx(juju, fast_forward):
        return juju.wait(ready, error=error, delay=delay, timeout=timeout, successes=successes)


def get_unit_url(juju: jubilant.Juju, application, unit, port, protocol="http"):
    """Return unit URL from the model.

    Args:
        juju: Jubilant Juju object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: http).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}
    """
    status = juju.status()
    address = status.apps[application].units[f"{application}/{unit}"].address
    return f"{protocol}://{address}:{port}"


def get_application_url(juju: jubilant.Juju, application, port):
    """Returns application URL from the model.

    Args:
        juju: Jubilant Juju object.
        application: Name of the application.
        port: Port number of the URL.

    Returns:
        Application URL of the form http://{address}:{port}
    """
    status = juju.status()
    address = status.apps[application].address
    return f"http://{address}:{port}"


def scale(juju: jubilant.Juju, app, units):
    """Scale the application to the provided number and wait for idle.

    Args:
        juju: Jubilant Juju object.
        app: Application to be scaled.
        units: Number of units required.
    """
    current_units = len(juju.status().apps[app].units)
    if units > current_units:
        juju.add_unit(app, num_units=units - current_units)
    elif units < current_units:
        juju.remove_unit(app, num_units=current_units - units)

    wait_for_apps(
        juju,
        [app],
        status="active",
        idle_period=30,
        raise_on_blocked=True,
        timeout=600,
        wait_for_exact_units=units,
    )


def get_memberships(url):
    """Return membership from Ranger.

    Args:
        url: Ranger unit address.

    Returns:
        membership: Ranger membership.

    Raises:
        Exception: requests exception.
    """
    url = f"{url}/service/xusers/groupusers"
    try:
        response = requests.get(url, headers=HEADERS, auth=RANGER_AUTH, timeout=20)
    except requests.exceptions.RequestException:
        logger.exception("An exception has occurred while getting Ranger memberships:")
        raise
    data = json.loads(response.text)
    logger.info(data)
    if not data.get("vXGroupUsers"):
        return None
    group = data["vXGroupUsers"][0].get("name")
    user_id = data["vXGroupUsers"][0].get("userId")
    membership = (group, user_id)
    return membership
