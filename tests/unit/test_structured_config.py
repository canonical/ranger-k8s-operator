#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import logging

import pytest
from ops.testing import Harness

from charm import RangerK8SCharm

logger = logging.getLogger(__name__)


@pytest.fixture
def _harness():
    """Harness setup for tests."""
    _harness = Harness(RangerK8SCharm)
    _harness.begin_with_initial_hooks()
    return _harness


def test_config_parsing_parameters_integer_values(_harness) -> None:
    """Check that integer fields are parsed correctly."""
    integer_fields = [
        "sync-interval",
    ]
    erroneus_values = [2147483648, -2147483649]
    valid_values = [3600000, 36000000, 86400000]
    for field in integer_fields:
        check_invalid_values(_harness, field, erroneus_values)
        check_valid_values(_harness, field, valid_values)


def test_string_values(_harness) -> None:
    """Test specific parameters for each field."""
    erroneus_values = ["test-value", "foo", "bar"]

    # charm-function
    check_invalid_values(_harness, "charm-function", erroneus_values)
    accepted_values = ["admin", "usersync"]
    check_valid_values(_harness, "charm-function", accepted_values)

    # sync-ldap-url
    check_invalid_values(_harness, "sync-ldap-url", erroneus_values)
    accepted_values = ["ldap://ldap-k8s:3893", "ldaps://example-host:636"]
    check_valid_values(_harness, "sync-ldap-url", accepted_values)


def test_password_fields(_harness) -> None:
    """Test password fields validation."""
    erroneous_passwords = [
        "onlyletters",  # No numbers
        "12345678",  # No letters
        "NoSpecialChar123",  # No special characters
        "Short1!",  # Too short
    ]

    valid_passwords = [
        "Valid1Pass!",
        "AnotherValid2#Password",
        "Password1$",
        "P@ssw0rd1234",
    ]

    check_invalid_values(
        _harness, "ranger-admin-password", erroneous_passwords
    )
    check_valid_values(_harness, "ranger-admin-password", valid_passwords)

    check_invalid_values(
        _harness, "ranger-usersync-password", erroneous_passwords
    )
    check_valid_values(_harness, "ranger-usersync-password", valid_passwords)


def check_valid_values(_harness, field: str, accepted_values: list) -> None:
    """Check the correctness of the passed values for a field.

    Args:
        _harness: Harness object.
        field: The configuration field to test.
        accepted_values: List of accepted values for this field.
    """
    for value in accepted_values:
        _harness.update_config({field: value})
        assert _harness.charm.config[field] == value


def check_invalid_values(_harness, field: str, erroneus_values: list) -> None:
    """Check the incorrectness of the passed values for a field.

    Args:
        _harness: Harness object.
        field: The configuration field to test.
        erroneus_values: List of invalid values for this field.
    """
    for value in erroneus_values:
        _harness.update_config({field: value})
        with pytest.raises(ValueError):
            _ = _harness.charm.config[field]
