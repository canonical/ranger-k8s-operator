#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Ranger charm."""

import logging
import re
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import root_validator, validator

logger = logging.getLogger(__name__)


class BaseEnumStr(str, Enum):
    """Base class for string enum."""

    def __str__(self) -> str:
        """Return the value as a string.

        Returns:
            string of config value
        """
        return str(self.value)


class FunctionType(str, Enum):
    """Enum for the `charm-function` field."""

    admin = "admin"
    usersync = "usersync"


class SearchScope(BaseEnumStr):
    """Enum for LDAP search scope fields."""

    base = "base"
    one = "one"
    sub = "sub"


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    system_users: str
    ldap_credentials: Optional[str]
    ranger_admin_password: str
    ranger_usersync_password: str
    sync_ldap_url: Optional[str]
    sync_ldap_bind_dn: Optional[str]
    sync_ldap_bind_password: Optional[str]
    sync_ldap_search_base: Optional[str]
    sync_ldap_user_object_class: Optional[str]
    sync_group_object_class: Optional[str]
    sync_ldap_user_search_base: Optional[str]
    sync_group_user_map_sync_enabled: Optional[bool]
    sync_group_search_enabled: Optional[bool]
    sync_group_member_attribute_name: Optional[str]
    sync_group_search_base: Optional[str]
    sync_ldap_user_search_scope: Optional[SearchScope]
    sync_ldap_group_search_scope: Optional[SearchScope]
    sync_ldap_user_search_filter: Optional[str]
    sync_ldap_user_name_attribute: Optional[str]
    sync_ldap_user_group_name_attribute: Optional[str]
    sync_ldap_deltasync: bool
    sync_interval: Optional[int]
    policy_mgr_url: Optional[str]
    charm_function: FunctionType
    lookup_timeout: int
    enforce_strict_reconciliation: bool

    @validator("*", pre=True)
    @classmethod
    def blank_string(cls, value):
        """Check for empty strings.

        Args:
            value: configuration value

        Returns:
            None in place of empty string or value
        """
        if value == "":
            return None
        return value

    @validator("sync_interval")
    @classmethod
    def sync_interval_validator(cls, value: str) -> Optional[int]:
        """Check validity of `sync_interval` field.

        Args:
            value: sync-interval value

        Returns:
            int_value: integer for sync-interval configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 3600 <= int_value <= 86400:
            return int_value
        raise ValueError("Value out of range.")

    @validator("sync_ldap_url")
    @classmethod
    def sync_ldap_url_validator(cls, value: Optional[str]) -> Optional[str]:
        """Check validity of `sync_ldap_url` field.

        Args:
            value: LDAP URL value

        Returns:
            LDAP URL value

        Raises:
            ValueError: If the URL is incorrectly formatted.
        """
        if value is None:
            return value

        ldap_url_pattern = r"^ldaps?://.*:\d+$"
        if re.match(ldap_url_pattern, value) is not None:
            return value
        raise ValueError("Value incorrectly formatted.")

    @root_validator(skip_on_failure=True)
    @classmethod
    def ldap_credentials_completeness_validator(cls, values):
        """Require every LDAP credential field when an LDAP secret is supplied.

        Args:
            values: Parsed configuration values.

        Returns:
            The validated configuration values.

        Raises:
            ValueError: If an LDAP secret omits a required value.
        """
        if not values.get("ldap_credentials"):
            return values

        required_fields = (
            "sync_ldap_url",
            "sync_ldap_bind_dn",
            "sync_ldap_bind_password",
            "sync_ldap_search_base",
            "sync_ldap_user_search_base",
            "sync_group_search_base",
        )
        missing_keys = [
            field.replace("_", "-") for field in required_fields if values.get(field) is None
        ]
        if missing_keys:
            raise ValueError(
                "ldap-credentials secret is missing required keys: " + ", ".join(missing_keys)
            )
        return values

    @validator("policy_mgr_url")
    @classmethod
    def policy_mgr_url_validator(cls, value: Optional[str]) -> Optional[str]:
        """Validate the policy manager URL format.

        Args:
            value: Policy manager URL.

        Returns:
            The validated URL.

        Raises:
            ValueError: If the URL does not use HTTP(S) or lacks a hostname.
        """
        if value is None:
            return value

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Value incorrectly formatted.")
        try:
            parsed.port
        except ValueError as err:
            raise ValueError("Value incorrectly formatted.") from err
        return value

    @validator("lookup_timeout")
    @classmethod
    def lookup_timeout_validator(cls, value: str) -> Optional[int]:
        """Check validity of `lookup_timeout` field.

        Args:
            value: timeout value

        Returns:
            int_value: integer for service configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 1000 <= int_value <= 10000:
            return int_value
        raise ValueError("Value out of range.")

    @validator("ranger_admin_password", "ranger_usersync_password")
    @classmethod
    def password_validator(cls, value: str) -> str:
        r"""Validate if the password meets the following requirements.

        - Minimum 8 characters in length
        - Contains at least one uppercase character
        - Contains at least one lowercase character
        - Contains at least one numeric character
        - Does not contain `"`, `'`, `\`, or `` ` ``

        Args:
            value: The password to validate.

        Returns:
            value: The validated password if it meets the requirements.

        Raises:
            ValueError: If the password does not meet the requirements.
        """
        pattern = re.compile(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?!.*[\"'\\`]).{8,}$")
        if pattern.match(value):
            return value
        raise ValueError("Password does not match requirements.")

    @root_validator
    @classmethod
    def usersync_policy_mgr_url_validator(cls, values):
        """Require a policy manager URL for usersync deployments.

        Args:
            values: Parsed configuration values.

        Returns:
            The validated configuration values.

        Raises:
            ValueError: If usersync is configured without a policy manager URL.
        """
        if values.get("charm_function") == FunctionType.usersync and not values.get(
            "policy_mgr_url"
        ):
            raise ValueError("policy-mgr-url is required when charm-function is usersync.")
        return values
