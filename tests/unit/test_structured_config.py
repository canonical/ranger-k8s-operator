#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import logging

import pytest
from ops import testing

from charm import RangerK8SCharm

logger = logging.getLogger(__name__)

SYSTEM_USERS_SECRET = testing.Secret(
    {
        "admin": "RangerAdmin1",
        "rangerusersync": "RangerUsersync1",
    }
)


@pytest.fixture
def ctx():
    """Scenario context for the charm."""
    return testing.Context(RangerK8SCharm)


def _state(config=None, secret=SYSTEM_USERS_SECRET):
    """Build a Scenario state with a system-users secret.

    Args:
        config: Configuration overrides.
        secret: The system-users secret available to the charm.

    Returns:
        A state configured to resolve the supplied secret.
    """
    return testing.State(
        config={"system-users": secret.id, **(config or {})},
        secrets={secret},
    )


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
    accepted_values = ["admin"]
    check_valid_values(ctx, "charm-function", accepted_values)
    state = _state(
        config={
            "charm-function": "usersync",
            "policy-mgr-url": "http://ranger-k8s:6080",
        }
    )
    with ctx(ctx.on.config_changed(), state) as manager:
        assert manager.charm.config["charm-function"] == "usersync"

    # sync-ldap-url
    check_invalid_values(ctx, "sync-ldap-url", erroneus_values)
    accepted_values = ["ldap://ldap-k8s:3893", "ldaps://example-host:636"]
    check_valid_values(ctx, "sync-ldap-url", accepted_values)


def test_ldap_search_scopes(ctx) -> None:
    """LDAP search scopes accept only Ranger-supported values."""
    valid_scopes = ["base", "one", "sub"]
    invalid_scopes = ["test-value", "foo", "bar"]
    check_valid_values(ctx, "sync-ldap-user-search-scope", valid_scopes)
    check_valid_values(ctx, "sync-ldap-group-search-scope", valid_scopes)
    check_invalid_values(ctx, "sync-ldap-user-search-scope", invalid_scopes)
    check_invalid_values(ctx, "sync-ldap-group-search-scope", invalid_scopes)


def test_policy_mgr_url_values(ctx) -> None:
    """Policy manager URLs require an HTTP(S) URL with a hostname."""
    check_invalid_values(
        ctx,
        "policy-mgr-url",
        ["ranger-k8s:6080", "ldap://ranger-k8s:6080", "https:///ranger-k8s"],
    )
    check_valid_values(
        ctx,
        "policy-mgr-url",
        [
            "http://ranger-k8s.my-model.svc.cluster.local:6080",
            "https://host:443",
        ],
    )


def test_password_fields(ctx) -> None:
    """Passwords in system-users match Ranger's validation rules."""
    erroneous_passwords = [
        "Short1a",  # Too short
        "nouppercase1",  # No uppercase character
        "NOLOWERCASE1",  # No lowercase character
        "NoNumbersHere",  # No numeric character
        'Invalid"1Password',
        "Invalid'1Password",
        "Invalid\\1Password",
        "Invalid`1Password",
    ]

    valid_passwords = [
        "AnotherValid2#Password",
        "Password1$",
        "P@ssw0rd1234",
        "NoSpecialChar123",
    ]

    for password in erroneous_passwords:
        secret = testing.Secret({"admin": password, "rangerusersync": "RangerUsersync1"})
        with ctx(ctx.on.config_changed(), _state(secret=secret)) as manager:
            with pytest.raises(ValueError):
                _ = manager.charm.config

    for password in valid_passwords:
        secret = testing.Secret({"admin": password, "rangerusersync": password})
        with ctx(ctx.on.config_changed(), _state(secret=secret)) as manager:
            assert manager.charm.config["ranger-admin-password"] == password
            assert manager.charm.config["ranger-usersync-password"] == password


def test_empty_system_user_password_is_required(ctx) -> None:
    """An empty secret value produces Pydantic's required-field error."""
    secret = testing.Secret({"admin": "", "rangerusersync": "RangerUsersync1"})
    with ctx(ctx.on.config_changed(), _state(secret=secret)) as manager:
        with pytest.raises(ValueError, match=r"(?s)ranger_admin_password.*field required"):
            _ = manager.charm.config


def test_strict_reconciliation_configuration(ctx) -> None:
    """Strict reconciliation uses its declared default and accepts a toggle."""
    state = _state()
    with ctx(ctx.on.config_changed(), state) as manager:
        assert manager.charm.config["enforce-strict-reconciliation"] is True

    state = _state(
        config={
            "enforce-strict-reconciliation": False,
        }
    )
    with ctx(ctx.on.config_changed(), state) as manager:
        assert manager.charm.config["enforce-strict-reconciliation"] is False


def check_valid_values(ctx, field: str, accepted_values: list) -> None:
    """Check the correctness of the passed values for a field.

    Args:
        ctx: Scenario context.
        field: The configuration field to test.
        accepted_values: List of accepted values for this field.
    """
    for value in accepted_values:
        state = _state(config={field: value})
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
        state = _state(config={field: value})
        with ctx(ctx.on.config_changed(), state) as manager:
            with pytest.raises(ValueError):
                _ = manager.charm.config[field]
