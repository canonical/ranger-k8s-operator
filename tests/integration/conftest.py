# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm integration test config."""

import logging
from pathlib import Path

import jubilant
import pytest
from pytest import FixtureRequest

from integration.helpers import APP_NAME, POSTGRES_NAME, wait_for_apps

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="charm_image")
def charm_image_fixture(request: FixtureRequest) -> str:
    """The OCI image for charm."""
    charm_image = request.config.getoption("--ranger-image")
    assert charm_image, (
        "--ranger-image argument is required which should contain the name of the OCI image."
    )
    return charm_image


@pytest.fixture(scope="module", name="charm")
def charm_fixture(request: FixtureRequest) -> str | Path:
    """Fetch the path to charm."""
    charms = request.config.getoption("--charm-file")
    if charms:
        charm = charms[0]
    else:
        charm_dir = Path(__file__).resolve().parents[2]
        charms = list(charm_dir.glob("*.charm"))
        assert charms, f"No charms were found in {charm_dir.resolve()}"
        assert len(charms) == 1, f"Found more than one charm {charms}"
        charm = charms[0]

    path = Path(charm).resolve()
    assert path.is_file(), f"{path} is not a file"
    return path


@pytest.fixture(name="deploy", scope="module")
def deploy(juju: jubilant.Juju, charm: str, charm_image: str):
    """Deploy the app."""
    resources = {
        "ranger-image": charm_image,
    }
    juju.deploy(POSTGRES_NAME, channel="14", trust=True)
    wait_for_apps(juju, [POSTGRES_NAME], status="active", timeout=1000)

    juju.deploy(
        charm,
        app=APP_NAME,
        resources=resources,
        num_units=1,
        config={"ranger-usersync-password": "P@ssw0rd1234"},
    )
    wait_for_apps(juju, [APP_NAME], status="blocked", timeout=1000)

    juju.integrate(APP_NAME, POSTGRES_NAME)

    juju.model_config({"update-status-hook-interval": "1m"})
    wait_for_apps(
        juju,
        [APP_NAME, POSTGRES_NAME],
        status="active",
        timeout=1500,
        idle_period=30,
    )

    status = juju.status()
    assert status.apps[APP_NAME].units[f"{APP_NAME}/0"].workload_status.current == "active"


# Incremental test support: replaces pytest-operator's abort_on_fail marker.
# Once a test in a class marked @pytest.mark.incremental fails, the remaining
# tests in that class are xfailed. This is the recipe from the pytest docs.
_test_failed_incremental: dict[str, dict[tuple[int, ...], str]] = {}


def pytest_runtest_makereport(item, call):
    """Record the first failing test for each incremental-marked class."""
    if "incremental" in item.keywords:
        if call.excinfo is not None:
            cls_name = str(item.cls)
            parametrize_index = (
                tuple(item.callspec.indices.values()) if hasattr(item, "callspec") else ()
            )
            test_name = item.originalname or item.name
            _test_failed_incremental.setdefault(cls_name, {}).setdefault(
                parametrize_index, test_name
            )


def pytest_runtest_setup(item):
    """Xfail a test if an earlier test in its incremental-marked class failed."""
    if "incremental" in item.keywords:
        cls_name = str(item.cls)
        if cls_name in _test_failed_incremental:
            parametrize_index = (
                tuple(item.callspec.indices.values()) if hasattr(item, "callspec") else ()
            )
            test_name = _test_failed_incremental[cls_name].get(parametrize_index, None)
            if test_name is not None:
                pytest.xfail(f"previous test failed ({test_name})")
