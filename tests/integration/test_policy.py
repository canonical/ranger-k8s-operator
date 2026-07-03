# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm policy integration test."""

import logging

import jubilant
import pytest
from apache_ranger.client import ranger_client

from integration.helpers import (
    APP_NAME,
    RANGER_AUTH,
    TRINO_NAME,
    TRINO_SERVICE,
    get_unit_url,
    wait_for_apps,
)

logger = logging.getLogger(__name__)


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestPolicyRelation:
    """Integration tests for establishing a policy relation."""

    def test_create_service(self, juju: jubilant.Juju):
        """Validate the service `trino-service` has been created."""
        trino_config = {
            "charm-function": "all",
            "ranger-service-name": TRINO_SERVICE,
        }

        juju.deploy(
            TRINO_NAME,
            channel="edge",
            config=trino_config,
            trust=True,
        )
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

        juju.integrate(f"{APP_NAME}:policy", f"{TRINO_NAME}:policy")
        wait_for_apps(juju, [APP_NAME, TRINO_NAME], status="active", timeout=1500)

        url = get_unit_url(juju, application=APP_NAME, unit=0, port=6080)
        ranger = ranger_client.RangerClient(url, RANGER_AUTH)

        new_service = ranger.get_service(TRINO_SERVICE)
        logger.info(f"service: {new_service}")
        name = new_service.get("name")
        assert TRINO_SERVICE in name
