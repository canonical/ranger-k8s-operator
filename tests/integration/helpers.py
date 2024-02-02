#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import json
import logging
from pathlib import Path

import requests
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
POSTGRES_NAME = "postgresql-k8s"
APP_NAME = "ranger-k8s"
NGINX_NAME = "nginx-ingress-integrator"
TRINO_SERVICE = "trino-service"
TRINO_NAME = "trino-k8s"
RANGER_URL = "http://localhost:6080"
RANGER_AUTH = ("admin", "rangerR0cks!")
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
GROUP_MANAGEMENT = """\
    trino-service:
        users:
          - name: user1
            firstname: One
            lastname: User
            email: user1@canonical.com
        memberships:
          - groupname: commercial-systems
            users: [user1]
        groups:
          - name: commercial-systems
            description: commercial systems team
"""
SECURE_PWD = "ubuntuR0cks!"  # nosec


async def get_unit_url(
    ops_test: OpsTest, application, unit, port, protocol="http"
):
    """Return unit URL from the model.

    Args:
        ops_test: PyTest object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: https).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}
    """
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][application]["units"][
        f"{application}/{unit}"
    ]["address"]
    return f"{protocol}://{address}:{port}"


async def get_application_url(ops_test: OpsTest, application, port):
    """Returns application URL from the model.

    Args:
        ops_test: PyTest object.
        application: Name of the application.
        port: Port number of the URL.

    Returns:
        Application URL of the form http://{address}:{port}
    """
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][application].public_address
    return f"http://{address}:{port}"


async def scale(ops_test: OpsTest, app, units):
    """Scale the application to the provided number and wait for idle.

    Args:
        ops_test: PyTest object.
        app: Application to be scaled.
        units: Number of units required.
    """
    await ops_test.model.applications[app].scale(scale=units)

    # Wait for model to settle
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        idle_period=30,
        raise_on_blocked=True,
        timeout=600,
        wait_for_exact_units=units,
    )


async def get_memberships(ops_test: OpsTest, url):
    """Return membership from Ranger.

    Args:
        ops_test: PyTest object.
        url: Ranger unit address.

    Returns:
        membership: Ranger membership.

    Raises:
        Exception: requests exception.
    """
    url = f"{url}/service/xusers/groupusers"
    try:
        response = requests.get(
            url, headers=HEADERS, auth=RANGER_AUTH, timeout=20
        )
    except requests.exceptions.RequestException:
        logger.exception(
            "An exception has occurred while getting Ranger memberships:"
        )
        raise
    data = json.loads(response.text)
    logger.info(data)
    group = data["vXGroupUsers"][0].get("name")
    user_id = data["vXGroupUsers"][0].get("userId")
    membership = (group, user_id)
    return membership
