#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured config unit tests."""

import logging

import pytest
from ops import testing
from pydantic import ValidationError

from charm import RangerK8SCharm
from secret_models import LdapCredentials, SystemUsers

logger = logging.getLogger(__name__)

SYSTEM_USERS_SECRET = testing.Secret(
    {
        "admin": "RangerAdmin1",
        "rangerusersync": "RangerUsersync1",
    }
)
LDAP_CREDENTIALS = {
    "sync-ldap-url": "ldap://ldap-k8s:389",
    "sync-ldap-bind-dn": "cn=admin,dc=canonical,dc=com",
    "sync-ldap-bind-password": "admin",  # nosec
    "sync-ldap-search-base": "dc=canonical,dc=com",
    "sync-ldap-user-search-base": "dc=canonical,dc=com",
    "sync-group-search-base": "dc=canonical,dc=com",
}


@pytest.fixture
def ctx():
    """Scenario context for the charm."""
    return testing.Context(RangerK8SCharm)


def _state(config=None, secret=SYSTEM_USERS_SECRET, secrets=None):
    """Build a Scenario state with a system-users secret.

    Args:
        config: Configuration overrides.
        secret: The system-users secret available to the charm.
        secrets: Additional secrets available to the charm.

    Returns:
        A state configured to resolve the supplied secret.
    """
    return testing.State(
        config={"system-users": secret.id, **(config or {})},
        secrets={secret, *(secrets or set())},
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

    check_invalid_ldap_urls(erroneus_values)
    check_valid_ldap_urls(["ldap://ldap-k8s:3893", "ldaps://example-host:636"])


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
        with pytest.raises(ValidationError):
            SystemUsers(admin=password, rangerusersync="RangerUsersync1")

    for password in valid_passwords:
        users = SystemUsers(admin=password, rangerusersync=password)
        assert users.admin == password
        assert users.rangerusersync == password


def test_empty_system_user_password_is_required(ctx) -> None:
    """An empty secret value produces Pydantic's required-field error."""
    with pytest.raises(ValidationError, match=r"(?s)admin.*field required"):
        SystemUsers(admin="", rangerusersync="RangerUsersync1")


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


def check_valid_ldap_urls(accepted_values: list) -> None:
    """Check LDAP URLs supplied through ldap-credentials.

    Args:
        accepted_values: LDAP URLs expected to pass validation.
    """
    for value in accepted_values:
        assert (
            LdapCredentials(**{**LDAP_CREDENTIALS, "sync-ldap-url": value}).sync_ldap_url == value
        )


def check_invalid_ldap_urls(erroneous_values: list) -> None:
    """Check malformed LDAP URLs supplied through ldap-credentials.

    Args:
        erroneous_values: LDAP URLs expected to fail validation.
    """
    for value in erroneous_values:
        with pytest.raises(ValidationError):
            LdapCredentials(**{**LDAP_CREDENTIALS, "sync-ldap-url": value})
