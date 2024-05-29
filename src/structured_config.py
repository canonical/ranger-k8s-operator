#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Superset charm."""
import logging
import re
from enum import Enum
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

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


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    ranger_admin_password: str
    tls_secret_name: str
    external_hostname: str
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
    sync_ldap_user_search_scope: Optional[str]
    sync_ldap_group_search_scope: Optional[str]
    sync_ldap_user_search_filter: Optional[str]
    sync_ldap_user_name_attribute: Optional[str]
    sync_ldap_user_group_name_attribute: Optional[str]
    sync_ldap_deltasync: bool
    sync_interval: Optional[int]
    ranger_usersync_password: Optional[str]
    policy_mgr_url: str
    charm_function: FunctionType

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
        """Check validity of `sqlalchemy_pool_size` field.

        Args:
            value: sync-interval value

        Returns:
            int_value: integer for sync-interval configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 3600000 <= int_value <= 86400000:
            return int_value
        raise ValueError("Value out of range.")

    @validator("sync_ldap_url")
    @classmethod
    def sync_ldap_url_validator(cls, value: str) -> Optional[str]:
        """Check validity of `sync_ldap_url` field.

        Args:
            value: sync-ldap-url value

        Returns:
            int_value: integer for sync-ldap-url configuration

        Raises:
            ValueError: in the case when the value incorrectly formatted.
        """
        ldap_url_pattern = r"^ldaps?://.*:\d+$"
        if re.match(ldap_url_pattern, value) is not None:
            return value
        raise ValueError("Value incorrectly formatted.")
