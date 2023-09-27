#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test helpers."""

import logging
from pathlib import Path

import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
POSTGRES_NAME = "postgresql-k8s"
APP_NAME = "ranger-k8s"
NGINX_NAME = "nginx-ingress-integrator"
RANGER_URL = "http://localhost:6080"
RANGER_AUTH = ("admin", "rangerR0cks!")


async def perform_ranger_integrations(ops_test: OpsTest, app_name):
    """Integrate Ranger charm with PostgreSQL charm.

    Args:
        ops_test: PyTest object
        app_name: The name of the Ranger application
    """
    await ops_test.model.integrate(app_name, POSTGRES_NAME)

    await ops_test.model.wait_for_idle(
        apps=[app_name], status="active", raise_on_blocked=False, timeout=1500
    )
    await ops_test.model.integrate(APP_NAME, NGINX_NAME)
    await ops_test.model.wait_for_idle(
        apps=[NGINX_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )


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
