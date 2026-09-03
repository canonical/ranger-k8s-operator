#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for Scenario-based unit tests."""

import pathlib

import pytest
import yaml
from ops.testing import Context

from charm import RangerK8SCharm

_CHARM_ROOT = pathlib.Path(__file__).parents[2]
_CHARMCRAFT = yaml.safe_load((_CHARM_ROOT / "charmcraft.yaml").read_text())
_META_KEYS = (
    "name",
    "summary",
    "description",
    "assumes",
    "containers",
    "resources",
    "storage",
    "requires",
    "provides",
    "peers",
)


def _charm_meta() -> dict:
    """Build charm metadata from charmcraft.yaml.

    Returns:
        The metadata fields understood by Scenario.
    """
    return {key: _CHARMCRAFT[key] for key in _META_KEYS if key in _CHARMCRAFT}


@pytest.fixture
def ctx():
    """Return a Scenario context configured from charmcraft.yaml.

    Returns:
        A configured Context for RangerK8SCharm.
    """
    return Context(
        RangerK8SCharm,
        meta=_charm_meta(),
        config=_CHARMCRAFT["config"],
        actions=_CHARMCRAFT["actions"],
    )
