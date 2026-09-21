#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured validation for Juju secret payloads."""

import re

from pydantic import BaseModel, Field, validator

PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?!.*[\"'\\`]).{8,}$")
COMPLEXITY_ERROR = "Password does not match requirements."


class SecretValidationError(ValueError):
    """Represent an actionable secret resolution or validation failure."""


def validate_password(value: str) -> str:
    r"""Validate Ranger's system-user password requirements.

    Args:
        value: Password to validate.

    Returns:
        The validated password.

    Raises:
        ValueError: If the password does not meet Ranger's requirements.
    """
    if PASSWORD_PATTERN.match(value):
        return value
    raise ValueError(COMPLEXITY_ERROR)


class UsersyncCredentials(BaseModel):
    """Usersync password stored in the usersync-credentials secret."""

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

    @validator("rangerusersync")
    @classmethod
    def password_validator(cls, value: str) -> str:
        """Validate Ranger's system-user password requirements.

        Args:
            value: Password to validate.

        Returns:
            The validated password.
        """
        return validate_password(value)


class LdapCredentials(BaseModel):
    """LDAP bind identity stored in the ldap-credentials secret."""

    sync_ldap_bind_dn: str = Field(alias="sync-ldap-bind-dn")
    sync_ldap_bind_password: str = Field(alias="sync-ldap-bind-password")

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
