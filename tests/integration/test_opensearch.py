# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Opensearch cross-controller integration test."""

import asyncio
import logging
import os

import pytest
import pytest_asyncio
import requests
from helpers import (
    APP_NAME,
    LXD_MODEL_CONFIG,
    METADATA,
    POSTGRES_NAME,
    get_or_add_model,
    get_unit_url,
)
from juju.controller import Controller
from juju.model import Model
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-opensearch", scope="module")
async def test_setup_models(ops_test: OpsTest):
    """Setup controllers and models.

    Args:
        ops_test: PyTest object.
    """
    lxd_controller_name = os.environ["LXD_CONTROLLER"]
    k8s_controller_name = os.environ["K8S_CONTROLLER"]

    _, lxd_model = await setup_lxd_controller_and_model(
        ops_test, lxd_controller_name
    )
    _, k8s_model = await setup_k8s_controller_and_model(
        ops_test, k8s_controller_name
    )
    await deploy_opensearch(lxd_model)
    await deploy_ranger(ops_test, k8s_model, lxd_model, lxd_controller_name)


async def setup_lxd_controller_and_model(
    ops_test: OpsTest, lxd_controller_name: str
):
    """Setup LXD controller and model.

    Args:
        ops_test: PyTest object.
        lxd_controller_name: The LXD controller name.

    Returns:
        lxd_controller: The LXD controller.
        lxd_model: The LXD model.
    """
    lxd_controller = Controller()
    await lxd_controller.connect(lxd_controller_name)
    lxd_model = await get_or_add_model(
        ops_test, lxd_controller, ops_test.model_name
    )
    await lxd_model.set_config(LXD_MODEL_CONFIG)
    return lxd_controller, lxd_model


async def setup_k8s_controller_and_model(
    ops_test: OpsTest, k8s_controller_name: str
):
    """Setup K8s controller and model.

    Args:
        ops_test: PyTest object.
        k8s_controller_name: The name of the K8s controller.

    Returns:
        k8s_controller: The K8s controller.
        K8s_model: The K8s model.
    """
    k8s_controller = Controller()
    await k8s_controller.connect(k8s_controller_name)
    k8s_model = await get_or_add_model(
        ops_test, k8s_controller, ops_test.model_name
    )
    await k8s_model.set_config(
        {"logging-config": "<root>=WARNING; unit=DEBUG"}
    )
    return k8s_controller, k8s_model


async def deploy_opensearch(lxd_model: Model):
    """Deploy OpenSearch and related components.

    Args:
        lxd_model: The LXD model.
    """
    await asyncio.gather(
        lxd_model.deploy("ch:opensearch", num_units=2, channel="2/edge"),
        lxd_model.deploy(
            "self-signed-certificates", num_units=1, channel="edge"
        ),
    )
    await lxd_model.add_relation("self-signed-certificates", "opensearch")
    await lxd_model.create_offer("opensearch:opensearch-client")
    await lxd_model.wait_for_idle(
        apps=["opensearch"],
        status="active",
        raise_on_blocked=False,
        timeout=3000,
    )


async def deploy_ranger(
    ops_test: OpsTest,
    k8s_model: Model,
    lxd_model: Model,
    lxd_controller_name: str,
):
    """Deploy Ranger and integrate with OpenSearch.

    Args:
        ops_test: PyTest object.
        k8s_model: The K8s model for Ranger deployment.
        lxd_model: The LXD model for OpenSearch deployment.
        lxd_controller_name: The name of the LXD controller.
    """
    charm = await ops_test.build_charm(".")
    resources = {
        "ranger-image": METADATA["resources"]["ranger-image"][
            "upstream-source"
        ]
    }
    await asyncio.gather(
        k8s_model.deploy(POSTGRES_NAME, channel="14", trust=True),
        k8s_model.deploy(
            charm, resources=resources, application_name=APP_NAME
        ),
    )
    await k8s_model.wait_for_idle(
        apps=[POSTGRES_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    logger.info("Integrating Ranger and Postgresql")
    await k8s_model.integrate(APP_NAME, POSTGRES_NAME)
    await k8s_model.consume(
        f"admin/{lxd_model.name}.opensearch",
        controller_name=lxd_controller_name,
    )

    logger.info("Integrating Ranger and OpenSearch")
    await k8s_model.integrate(APP_NAME, "opensearch")
    async with ops_test.fast_forward():
        await k8s_model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1500,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-opensearch")
class TestOpenSearch:
    """Integration tests for auditing Ranger charm."""

    async def test_ranger_audits(self, ops_test: OpsTest):
        """Perform GET request on the Trino UI host."""
        url = await get_unit_url(
            ops_test, application=APP_NAME, unit=0, port=6080
        )
        audit_url = f"{url}/service/assets/accessAudit"
        logger.info("curling app address: %s", audit_url)

        response = requests.get(audit_url, timeout=300, verify=False)  # nosec
        assert response.status_code == 200
