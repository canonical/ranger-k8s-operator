# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with the Ranger API for user/group operations."""

import copy
import json
import logging

import requests
import yaml
from apache_ranger.exceptions import RangerServiceException
from ops.charm import CharmBase
from ops.model import ActiveStatus

from literals import (
    ADMIN_USER,
    ENDPOINT_MAPPING,
    EXPECTED_KEYS,
    HEADERS,
    MEMBER_TYPE_MAPPING,
    RANGER_URL,
    SYSTEM_GROUPS,
)
from utils import (
    generate_random_string,
    handle_service_error,
    log_event_handler,
    retry,
)

logger = logging.getLogger(__name__)


class RangerGroupManager:
    """This class handles the communication with the Ranger API."""

    def __init__(self, charm: CharmBase):
        """Construct RangerGroupManager object.

        Args:
            charm: the charm for which this relation is provided
        """
        self.charm = charm

    @property
    def _auth(self):
        """Return the authentication credentials."""
        admin_password = self.charm.config["ranger-admin-password"]
        auth = (ADMIN_USER, admin_password)
        return auth

    @log_event_handler(logger)
    def _handle_synchronize_file(self, event):
        """Synchronize the data from the config file with the Ranger API.

        Args:
            event: The config changed event that triggered the synchronization.
        """
        if self.charm.unit.status != ActiveStatus("Status check: UP"):
            logger.debug("Status not active, event deferred.")
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        config_data = yaml.safe_load(
            self.charm.config["user-group-configuration"]
        )
        for key in config_data:
            data = next(iter(config_data.values()))
            relation_data = copy.deepcopy(data)
            self._synchronize(key, data, event)
            self._add_to_relations(key, relation_data)

    def _synchronize(self, key, data, event):
        """Synchronize data with the Ranger API.

        Args:
            key: relation_id from configuration file.
            data: Data to synchronize with the Ranger API.
            event: configuration changed event.
        """
        try:
            self._sync(data["groups"], "group")
            self._sync(data["users"], "user")
            self._sync(data["memberships"], "membership")
            logger.info(f"Synchronized users and groups for {key}")
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to sync {key}."
            )
            event.defer()

    @handle_service_error
    def _sync(self, config, member_type):
        """Synchronize apply values with the Ranger API.

        Args:
            config: Values to synchronize.
            member_type: The type of Ranger member (group, user or membership).
        """
        # Get existing values
        existing = self._get_existing_values(member_type)

        # Get values to apply
        apply = self._transform_apply_values(config, member_type)

        # Create members
        to_create = [item for item in apply if item not in existing]
        for value in to_create:
            if member_type == "membership":
                fields = value
            if member_type in ["user", "group"]:
                fields = [
                    member for member in config if member["name"] == value
                ][0]
            self._create_members(member_type, fields, value)

        if member_type == "user":
            return

        # delete groups and memberships
        to_delete = [item for item in existing if item not in apply]
        for value in to_delete:
            self._delete_members(member_type, value)

    @handle_service_error
    def _get_existing_values(self, member_type):
        """Retrieve existing members from the Ranger API.

        Args:
            member_type: The type of Ranger member (group, user or membership).

        Returns:
            values: Existing members from the Ranger API.
        """
        j = self._query_members(member_type)
        key = MEMBER_TYPE_MAPPING[member_type]
        all_fields = j[key]

        values = []
        member_id = {}
        id_mapping = self.charm._state.id_mapping or {}

        for member in all_fields:
            if member_type in ["group", "user"]:
                key = member.get("name")
            elif member_type == "membership":
                key = (member["name"], member["userId"])
            values.append(key)
            member_id[str(key)] = member.get("id")

        id_mapping[member_type] = member_id
        self.charm._state.id_mapping = id_mapping
        return values

    @handle_service_error
    @retry(max_retries=3, delay=2, backoff=2)
    def _query_members(self, member_type):
        """Send a GET request to the Ranger API for members.

        Args:
            member_type: The type of Ranger member (group, user or membership).

        Returns:
            Response from the GET request.
        """
        endpoint = ENDPOINT_MAPPING[member_type]
        url = f"{RANGER_URL}/service/xusers/{endpoint}"
        response = requests.get(
            url, headers=HEADERS, auth=self._auth, timeout=10
        )
        j = json.loads(response.text)
        return j

    @handle_service_error
    @retry(max_retries=3, delay=2, backoff=2)
    def _delete_members(self, member_type, value):
        """Send a DELETE request to the Ranger API for a member.

        Args:
            member_type: The type of Ranger member (group, user or membership).
            value: The identifying value of the member to delete.
        """
        if member_type == "group" and value in SYSTEM_GROUPS:
            return

        if member_type == "membership" and value[0] in SYSTEM_GROUPS:
            return

        ids = self.charm._state.id_mapping[member_type]
        value_id = ids[str(value)]

        endpoint = ENDPOINT_MAPPING[member_type]
        url = f"{RANGER_URL}/service/xusers/{endpoint}/{value_id}"
        response = requests.delete(
            url, headers=HEADERS, auth=self._auth, timeout=10
        )

        if response.status_code == 204:
            logger.info(f"Deleted {member_type}: {value_id}")
        else:
            logger.info(f"Unable to delete {member_type}: {value_id}")

    @handle_service_error
    @retry(max_retries=3, delay=2, backoff=2)
    def _create_members(self, member_type, fields, value):
        """Send a POST request to create a member in the Ranger API.

        Args:
            member_type: The type of Ranger member (group, user or membership).
            fields: Fields required for creating the payload.
            value: The identifying value for the member. Name or Tuple.
        """
        payload = create_payload(member_type, fields)
        endpoint = ENDPOINT_MAPPING[member_type]
        url = f"{RANGER_URL}/service/xusers/{endpoint}"

        response = requests.post(
            url,
            headers=HEADERS,
            json=payload,
            auth=self._auth,
            timeout=10,
        )
        if response.status_code == 200:
            self._update_id_mapping(response, member_type)
            logger.info(f"Created {member_type}: {value}")
        else:
            logger.info(f"Unable to create {member_type}: {value}")

    def _update_id_mapping(self, response, member_type):
        """Update ID mapping for members created during synchronization.

        Args:
            response: The http response.
            member_type: One of "user" or "group".
        """
        created_member = json.loads(response.text)
        member_id = created_member["id"]
        if member_type in ["user", "group"]:
            key = created_member["name"]
        elif member_type == "membership":
            key = (created_member["name"], created_member["userId"])

        id_mapping = self.charm._state.id_mapping
        id_mapping[member_type].update({str(key): member_id})
        self.charm._state.id_mapping = id_mapping

    @handle_service_error
    def _transform_apply_values(self, data, member_type):
        """Get list of users, groups or memberships to apply from configuration file.

        Args:
            data: User, group or membership data.
            member_type: The type of Ranger member (group, user or membership).

        Returns:
            List of users, groups or memberships to apply.
        """
        if member_type in ["user", "group"]:
            values = [member["name"] for member in data]
            return values

        user_id_mapping = self.charm._state.id_mapping["user"]

        membership_tuples = []
        for membership in data:
            for user in membership["users"]:
                user_id = user_id_mapping[user]
                member = (membership["groupname"], user_id)
                membership_tuples.append(member)

        return membership_tuples

    def _add_to_relations(self, key, data):
        """Add user-group configuration data to policy relations.

        Args:
            key: Key to identify the relation.
            data: User-group configuration data to add to the relation.
        """
        policy_relations = self.charm.model.relations.get("policy")
        if not policy_relations:
            return

        for relation in policy_relations:
            service_name = relation.data[self.charm.app].get("service_name")
            if key == service_name:
                yaml_string = yaml.dump(data)
                relation.data[self.charm.app].update(
                    {"user-group-configuration": yaml_string}
                )

    def _validate(self):  # noqa: C901
        """Validate user-group-configuration file values.

        Raises:
            ValueError: In case the file cannot be parsed.
                        In case the file cannot be converted to a dictionary.
                        In case relation key is missing.
                        In case user, group or membership values are missing.

        """
        # Validate data can be loaded from the file.
        try:
            data = yaml.safe_load(
                self.charm.config["user-group-configuration"]
            )
        except yaml.YAMLError as e:
            raise ValueError(
                "The configuration file is improperly formatted, unable to parse."
            ) from e

        # Validate resulting data is a dictionary.
        if not isinstance(data, dict):
            raise ValueError("The configuration file is improperly formatted.")

        for key in data.keys():
            # Validate the file has a service name.
            if any(keyword in key for keyword in EXPECTED_KEYS):
                raise ValueError(
                    "User management configuration file must have service keys."
                )

            # Validate the file contains only services that exist.
            if key not in service_names:
                raise ValueError(f"{key} does not match a related service.")

            # Validate that there are `user`, `group` and `membership` keys.
            for expected_key in EXPECTED_KEYS:
                if expected_key not in data[key]:
                    raise ValueError(
                        f"Missing '{expected_key}' values in the configuration file."
                    )


def create_payload(member_type, member):
    """Create a payload for a new user in the Ranger API.

    Args:
        member_type: The type of Ranger member (group, user or membership).
        member: The fields for creating the member.

    Returns:
        user_payload: User payload data for creating a new user.
    """
    if member_type == "user":
        password = generate_random_string(12)
        payload = {
            "name": member["name"],
            "password": password,
            "firstName": member["firstname"],
            "lastName": member["lastname"],
            "emailAddress": member["email"],
            "status": 1,
            "userSource": 1,
            "userRoleList": ["ROLE_USER"],
            "isVisible": 1,
        }
    if member_type == "group":
        payload = {
            "name": member["name"],
            "description": member["description"],
        }

    if member_type == "membership":
        payload = {
            "name": member[0],
            "userId": member[1],
        }
    return payload
