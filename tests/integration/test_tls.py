#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL TLS integration tests."""

import jubilant
import pytest
import requests

from integration.helpers import (
    APP_NAME,
    POSTGRES_NAME,
    get_unit_url,
    wait_for_apps,
    wait_for_ranger_jdbc_ssl,
)

CERTIFICATES_NAME = "self-signed-certificates"
RANGER_CA_PATH = "/etc/ranger/postgresql-ca.crt"
RANGER_SITE_CONFIG = "/usr/lib/ranger/admin/ews/webapp/WEB-INF/classes/conf/ranger-admin-site.xml"


@pytest.fixture(name="deploy_tls", scope="module")
def deploy_tls_fixture(juju: jubilant.Juju, charm: str, charm_image: str):
    """Deploy TLS-enabled PostgreSQL before relating Ranger to it."""
    juju.deploy(CERTIFICATES_NAME, channel="latest/stable", trust=True)
    juju.deploy(POSTGRES_NAME, channel="14", trust=True)
    juju.integrate(f"{CERTIFICATES_NAME}:certificates", f"{POSTGRES_NAME}:certificates")
    wait_for_apps(juju, [CERTIFICATES_NAME, POSTGRES_NAME], status="active", timeout=1000)

    juju.deploy(charm, app=APP_NAME, resources={"ranger-image": charm_image}, num_units=1)
    wait_for_apps(juju, [APP_NAME], status="blocked", timeout=1000)
    juju.integrate(APP_NAME, POSTGRES_NAME)
    wait_for_apps(juju, [APP_NAME, POSTGRES_NAME], status="active", timeout=1500, idle_period=30)


@pytest.mark.incremental
@pytest.mark.usefixtures("deploy_tls")
class TestPostgresTLS:
    """Ranger's PostgreSQL connection when the provider enables TLS."""

    def test_jdbc_url_verifies_server(self, juju: jubilant.Juju):
        """Ranger's JDBC URL requires verify-full against the managed CA."""
        config = juju.ssh(f"{APP_NAME}/0", f"cat {RANGER_SITE_CONFIG}", container="ranger")

        assert "sslmode=verify-full" in config
        assert f"sslrootcert={RANGER_CA_PATH}" in config

    def test_jdbc_sessions_use_tls(self, juju: jubilant.Juju):
        """Ranger's PostgreSQL sessions are encrypted and its API responds."""
        wait_for_ranger_jdbc_ssl(juju, ssl=True)

        response = requests.get(get_unit_url(juju, APP_NAME, 0, 6080), timeout=300)
        assert response.status_code == 200
