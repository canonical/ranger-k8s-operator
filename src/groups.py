# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with the Ranger API for user/group operations."""

import json
import logging

import requests
import yaml
from apache_ranger.exceptions import RangerServiceException
from ops.charm import CharmBase
from ops.model import ActiveStatus

from literals import ADMIN_USER, HEADERS, RANGER_URL, SYSTEM_GROUPS
from utils import generate_random_string, log_event_handler, retry

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
        if not self.charm.unit.status == ActiveStatus("Status check: UP"):
            logger.debug("Status not active, event deferred.")
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        config_data = self._read_file()
        for key in config_data:
            data = next(iter(config_data.values()))
            self._add_to_relations(key, data)
            self._synchronize(key, data, event)

    def _read_file(self):
        """Read the user-group-configuration from the charm config.

        Returns:
            Data read from the configuration file.
        """
        config = self.charm.config["user-group-configuration"]
        data = yaml.safe_load(config)
        return data

    def _synchronize(self, key, data, event):
        """Synchronize data with the Ranger API.

        Args:
            key: relation_id from configuration file.
            data: Data to synchronize with the Ranger API.
            event: configuration changed event.
        """
        try:
            self._sync(data["groups"], "groups")
            self._sync(data["users"], "users")
            self._sync_memberships(data["memberships"])
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to sync {key}."
            )
            event.defer()

    @retry(max_retries=3, delay=2, backoff=2)
    def _get_request(self, member_type):
        """Send a GET request to the Ranger API for members.

        Args:
            member_type: The type of member to retrieve (groups or users).

        Returns:
            Response from the GET request.
        """
        url = f"{RANGER_URL}/service/xusers/{member_type}"
        response = requests.get(url, headers=HEADERS, auth=self._auth)
        return response

    @retry(max_retries=3, delay=2, backoff=2)
    def _delete_request(self, member_type, value_id):
        """Send a DELETE request to the Ranger API for a member.

        Args:
            member_type: The type of member to delete (groups or users).
            value_id: The ID of the member to delete.

        Returns:
            Response from the DELETE request.
        """
        url = f"{RANGER_URL}/service/xusers/{member_type}/{value_id}"
        response = requests.delete(url, headers=HEADERS, auth=self._auth)
        return response

    @retry(max_retries=3, delay=2, backoff=2)
    def _create_request(self, member_type, data):
        """Send a POST request to create a member in the Ranger API.

        Args:
            member_type: The type of member to create (groups or users).
            data: Data for the new member.

        Returns:
            Response from the POST request.
        """
        url = f"{RANGER_URL}/service/xusers/{member_type}"
        response = requests.post(
            url, headers=HEADERS, json=data, auth=self._auth
        )
        return response

    def _delete(self, member_type, value_id):
        """Delete a member in the Ranger API.

        Args:
            member_type: The type of member to delete (groups or users).
            value_id: The ID of the member to delete.

        Raises:
            RangerServiceException: when failing to delete a user, group or membership.
        """
        try:
            response = self._delete_request(member_type, value_id)
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to delete {member_type}, {value_id}:"
            )
            raise
        if response.status_code == 204:
            logger.debug(f"Deleted {member_type}: {value_id}")
        else:
            logger.info(f"Unable to delete {member_type}: {value_id}")

    def _create(self, member_type, name, values):
        """Create a member in the Ranger API.

        Args:
            member_type: The type of member to create (groups or users).
            name: Name of the member.
            values: Data for the new member.

        Raises:
            RangerServiceException: when failing to create a user, group or membership.
        """
        data = (
            create_user_payload(values) if member_type == "users" else values
        )

        try:
            response = self._create_request(member_type, data)
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to create {member_type}, {name}:"
            )
            raise

        if response.status_code == 200:
            logger.debug(f"Created {member_type}: {name}")
        else:
            logger.info(f"Unable to create {member_type}: {name}")

    def _get_existing_values(self, member_type):
        """Retrieve existing members from the Ranger API.

        Args:
            member_type: The type of member to retrieve (groups, users or groupusers).

        Returns:
            Existing members from the Ranger API.

        Raises:
            RangerServiceException: when failing to get existing user or group values.
        """
        try:
            response = self._get_request(member_type)
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to get {member_type}:"
            )
            raise

        j = json.loads(response.text)
        if member_type == "groups":
            output = j["vXGroups"]
        if member_type == "users":
            output = j["vXUsers"]
        if member_type == "groupusers":
            output = j["vXGroupUsers"]
        return output

    def _sync(self, apply_values, member_type):
        """Synchronize apply values with the Ranger API.

        Args:
            apply_values: Values to synchronize.
            member_type: The type of member to synchronize (groups or users).

        Raises:
            RangerServiceException: when failing to get existing user or group values.
        """
        try:
            existing_values = self._get_existing_values(member_type)
        except RangerServiceException:
            logger.exception(
                f"A Ranger Service Exception has occurred while attempting to get {member_type}"
            )
            raise
        if member_type == "groups":
            self._delete_groups(existing_values, apply_values)

        for value in apply_values:
            apply_name = value.get("name")
            matching = next(
                (
                    existing_value
                    for existing_value in existing_values
                    if existing_value.get("name") == apply_name
                ),
                None,
            )
            if not matching:
                try:
                    self._create(member_type, apply_name, value)
                except RangerServiceException as e:
                    logger.warning(
                        f"Unable to create {member_type}, {apply_name}, skipping. Error {e}"
                    )

    def _delete_groups(self, existing_groups, apply_groups):
        """Delete groups from the Ranger API.

        Args:
            existing_groups: Existing groups in the Ranger API.
            apply_groups: Groups which should exist after synchronization.
        """
        for group in existing_groups:
            existing_name = group.get("name")
            matching = next(
                (
                    apply_group
                    for apply_group in apply_groups
                    if apply_group.get("name") == existing_name
                ),
                None,
            )
            if existing_name in SYSTEM_GROUPS:
                continue
            if not matching:
                try:
                    group_id = group["id"]
                    self._delete("groups", group_id)
                except RangerServiceException as e:
                    logger.warning(
                        f"Could not delete group, {group_id}, skipping. Error: {e}"
                    )

    def _sync_memberships(self, apply_memberships):
        """Synchronize memberships with the Ranger API.

        Args:
            apply_memberships: Memberships to apply.

        Raises:
            RangerServiceException: when failing to synchronize group memberships.
        """
        try:
            (
                existing_memberships,
                existing_combinations,
            ) = self._get_existing_memberships()
            remaining_combinations = self._create_memberships(
                apply_memberships, existing_combinations
            )
            self._delete_memberships(
                remaining_combinations, existing_memberships
            )
        except RangerServiceException:
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to sync memberships:"
            )
            raise

    def _get_existing_memberships(self):
        """Retrieve existing group-user memberships from the Ranger API.

        Returns:
            existing_memberships: Existing memberships and related data.
            existing_combinations: Tuple of combinations of group-user memberships.

        Raises:
            RangerServiceException: when failure to get group memberships.
        """
        try:
            existing_memberships = self._get_existing_values("groupusers")
        except RangerServiceException:
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to get existing memberships:"
            )
            raise
        existing_combinations = {
            (membership["name"], membership["userId"])
            for membership in existing_memberships
        }
        return existing_memberships, existing_combinations

    def _get_user_ids(self, apply_memberships):
        """Map user names to user IDs for memberships.

        Args:
            apply_memberships: Memberships to apply.

        Returns:
            apply_memberships: Memberships to apply with user IDs instead of user names.

        Raises:
            RangerServiceException: on failure to get users.
        """
        try:
            users = self._get_existing_values("users")
        except RangerServiceException:
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to get user ids:"
            )
            raise

        user_id_mapping = {user["name"]: user["id"] for user in users}
        for members in apply_memberships:
            user_names = members["users"]
            user_ids = [
                user_id_mapping.get(user_name, user_name)
                for user_name in user_names
            ]
            members["users"] = user_ids
        return apply_memberships

    def _create_memberships(self, apply_memberships, existing_combinations):
        """Create group-user memberships in the Ranger API.

        Args:
            apply_memberships: Memberships to apply.
            existing_combinations: Memberships already in Ranger.

        Returns:
            existing_combinations: Set of remaining memberships to be deleted.

        Raises:
            RangerServiceException: on failure to get users.
        """
        try:
            apply_memberships = self._get_user_ids(apply_memberships)
        except RangerServiceException:
            logger.exception(
                "A Ranger Service Exception has occurred while attempting to get user ids:"
            )
            raise

        for apply_membership in apply_memberships:
            groupname = apply_membership["groupname"]
            for user_id in apply_membership["users"]:
                if (groupname, user_id) in existing_combinations:
                    # These already exist and need no further action.
                    existing_combinations.remove((groupname, user_id))
                else:
                    values = create_membership_payload(groupname, user_id)
                    try:
                        self._create(
                            "groupusers", (groupname, user_id), values
                        )
                    except RangerServiceException as e:
                        logger.warning(
                            f"Unable to create membership {groupname}, {user_id}: skipping. Error: {e}"
                        )
        return existing_combinations

    def _delete_memberships(
        self, remaining_combinations, existing_memberships
    ):
        """Delete group-user memberships in the Ranger API.

        Args:
            remaining_combinations: Set of group-user membership combinations to be deleted.
            existing_memberships: Existing group-user memberships in the Ranger API.
        """
        for groupname, user_id in remaining_combinations:
            delete_id = next(
                item["id"]
                for item in existing_memberships
                if item["name"] == groupname and item["userId"] == user_id
            )
            try:
                self._delete("groupusers", delete_id)
            except RangerServiceException as e:
                logger.warning(
                    f"Unable to delete membership {delete_id}, skipping. Error: {e}"
                )

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
            if key == f"relation_{relation.id}":
                yaml_string = yaml.dump(data)
                relation.data[self.charm.app].update(
                    {"user-group-configuration": yaml_string}
                )


def create_user_payload(user):
    """Create a payload for a new user in the Ranger API.

    Args:
        user: User data containing name, first name, last name, email, etc.

    Returns:
        user_payload: User payload data for creating a new user.
    """
    password = generate_random_string(12)
    user_payload = {
        "name": user["name"],
        "password": password,
        "firstName": user["firstname"],
        "lastName": user["lastname"],
        "emailAddress": user["email"],
        "status": 1,
        "userSource": 1,
        "userRoleList": ["ROLE_USER"],
        "isVisible": 1,
    }
    return user_payload


def create_membership_payload(groupname, user_id):
    """Create a payload for a group-user membership in the Ranger API.

    Args:
        groupname: Name of the group.
        user_id: ID of the user.

    Returns:
        Membership payload data for creating a group-user membership.
    """
    return {
        "name": groupname,
        "userId": user_id,
    }
