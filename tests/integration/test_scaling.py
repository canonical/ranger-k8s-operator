# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm scaling integration test."""

import logging

import jubilant
import pytest
import requests

from integration.helpers import APP_NAME, get_application_url, scale

logger = logging.getLogger(__name__)


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy")
class TestScaling:
    """Integration tests for scaling Ranger charm."""

    def test_scaling_up(self, juju: jubilant.Juju):
        """Scale Ranger charm up to 2 units."""
        scale(juju, app=APP_NAME, units=2)
        assert len(juju.status().apps[APP_NAME].units) == 2

        url = get_application_url(juju, application=APP_NAME, port=6080)
        logger.info("curling app address: %s", url)
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200

    def test_scaling_down(self, juju: jubilant.Juju):
        """Scale Ranger charm down to 1 unit."""
        scale(juju, app=APP_NAME, units=1)
        assert len(juju.status().apps[APP_NAME].units) == 1

        url = get_application_url(juju, application=APP_NAME, port=6080)
        logger.info("curling app address: %s", url)
        response = requests.get(url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200
