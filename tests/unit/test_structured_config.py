#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import logging

import pytest
from ops import testing

from charm import RangerK8SCharm

logger = logging.getLogger(__name__)


@pytest.fixture
def ctx():
    """Scenario context for the charm."""
    return testing.Context(RangerK8SCharm)


def test_config_parsing_parameters_integer_values(ctx) -> None:
    """Check that integer fields are parsed correctly."""
    integer_fields = [
        "sync-interval",
    ]
    erroneus_values = [2147483648, -2147483649]
    valid_values = [3600, 36000, 86400]
    for field in integer_fields:
        check_invalid_values(ctx, field, erroneus_values)
        check_valid_values(ctx, field, valid_values)


def test_string_values(ctx) -> None:
    """Test specific parameters for each field."""
    erroneus_values = ["test-value", "foo", "bar"]

    # charm-function
    check_invalid_values(ctx, "charm-function", erroneus_values)
    accepted_values = ["admin", "usersync"]
    check_valid_values(ctx, "charm-function", accepted_values)

    # sync-ldap-url
    check_invalid_values(ctx, "sync-ldap-url", erroneus_values)
    accepted_values = ["ldap://ldap-k8s:3893", "ldaps://example-host:636"]
    check_valid_values(ctx, "sync-ldap-url", accepted_values)


def test_password_fields(ctx) -> None:
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

    check_invalid_values(ctx, "ranger-admin-password", erroneous_passwords)
    check_valid_values(ctx, "ranger-admin-password", valid_passwords)

    check_invalid_values(ctx, "ranger-usersync-password", erroneous_passwords)
    check_valid_values(ctx, "ranger-usersync-password", valid_passwords)


def check_valid_values(ctx, field: str, accepted_values: list) -> None:
    """Check the correctness of the passed values for a field.

    Args:
        ctx: Scenario context.
        field: The configuration field to test.
        accepted_values: List of accepted values for this field.
    """
    for value in accepted_values:
        state = testing.State(config={field: value})
        with ctx(ctx.on.config_changed(), state) as manager:
            assert manager.charm.config[field] == value


def check_invalid_values(ctx, field: str, erroneus_values: list) -> None:
    """Check the incorrectness of the passed values for a field.

    Args:
        ctx: Scenario context.
        field: The configuration field to test.
        erroneus_values: List of invalid values for this field.
    """
    for value in erroneus_values:
        state = testing.State(config={field: value})
        with ctx(ctx.on.config_changed(), state) as manager:
            with pytest.raises(ValueError):
                _ = manager.charm.config[field]
