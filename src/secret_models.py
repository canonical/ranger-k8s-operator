#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured validation for Juju secret payloads."""

import re

from pydantic import BaseModel, Field, validator


class SecretValidationError(ValueError):
    """Represent an actionable secret resolution or validation failure."""


class SystemUsers(BaseModel):
    """System-user passwords stored in the system-users secret."""

    admin: str
    rangerusersync: str

    @validator("*", pre=True)
    @classmethod
    def required_value(cls, value):
        """Reject blank secret values without coercing them.

        Args:
            value: Secret value to validate.

        Returns:
            The non-blank secret value.

        Raises:
            ValueError: If the secret value is blank.
        """
        if value == "":
            raise ValueError("field required")
        return value

    @validator("admin", "rangerusersync")
    @classmethod
    def password_validator(cls, value: str) -> str:
        r"""Validate Ranger's system-user password requirements.

        Args:
            value: Password to validate.

        Returns:
            The validated password.

        Raises:
            ValueError: If the password does not meet Ranger's requirements.
        """
        pattern = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?!.*[\"'\\`]).{8,}$")
        if pattern.match(value):
            return value
        raise ValueError("Password does not match requirements.")


class LdapCredentials(BaseModel):
    """LDAP connection values stored in the ldap-credentials secret."""

    sync_ldap_url: str = Field(alias="sync-ldap-url")
    sync_ldap_bind_dn: str = Field(alias="sync-ldap-bind-dn")
    sync_ldap_bind_password: str = Field(alias="sync-ldap-bind-password")
    sync_ldap_search_base: str = Field(alias="sync-ldap-search-base")
    sync_ldap_user_search_base: str = Field(alias="sync-ldap-user-search-base")
    sync_group_search_base: str = Field(alias="sync-group-search-base")

    class Config:
        """Configure aliases for Juju secret keys."""

        allow_population_by_field_name = True

    @validator("*", pre=True)
    @classmethod
    def required_value(cls, value):
        """Reject blank secret values without coercing them.

        Args:
            value: Secret value to validate.

        Returns:
            The non-blank secret value.

        Raises:
            ValueError: If the secret value is blank.
        """
        if value == "":
            raise ValueError("field required")
        return value

    @validator("sync_ldap_url")
    @classmethod
    def sync_ldap_url_validator(cls, value: str) -> str:
        """Validate the LDAP URL format.

        Args:
            value: LDAP URL to validate.

        Returns:
            The validated LDAP URL.

        Raises:
            ValueError: If the URL is incorrectly formatted.
        """
        ldap_url_pattern = r"^ldaps?://.*:\d+$"
        if re.match(ldap_url_pattern, value) is not None:
            return value
        raise ValueError("Value incorrectly formatted.")
