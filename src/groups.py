# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with the Ranger API for user/group operations."""

import logging
import requests
import yaml
import json
from literals import (
    RANGER_URL,
    HEADERS,
    ADMIN_USER,
    SYSTEM_GROUPS,
)
from utils import generate_random_string, log_event_handler
from ops.charm import CharmBase
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)

class RangerGroupManager():
    """This class handles the communication with the Ranger API."""

    def __init__(self, charm: CharmBase):
        """Construct RangerGroupManager object.

        Args:
            charm: the charm for which this relation is provided
        """
        self.charm = charm

    @property
    def _auth(self):
        admin_password = self.charm.config["ranger-admin-password"]
        auth = (ADMIN_USER, admin_password)
        return auth

    @log_event_handler(logger)
    def _handle_synchronize_file(self, event):
        if not self.charm.unit.status == ActiveStatus('Status check: UP'):
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        self.synchronize()

    def synchronize(self):
        data = self._read_file()
        self._sync(data["groups"], "groups")
        self._sync(data["users"], "users")
        self._sync_memberships(data["memberships"])

    def _read_file(self):
        config = self.charm.config["user-group-configuration"]
        data = yaml.safe_load(config)
        return data

    def _get_request(self, object_type):
        url = f"{RANGER_URL}/service/xusers/{object_type}"
        response = requests.get(url, headers=HEADERS, auth=self._auth)
        return response


    def _delete_request(self, object_type, id):
        url = f"{RANGER_URL}/service/xusers/{object_type}/{id}"
        response = requests.delete(url, headers=HEADERS, auth=self._auth)
        return response


    def _create_request(self, object_type, data):
        url = f"{RANGER_URL}/service/xusers/{object_type}"
        response = requests.post(url, headers=HEADERS, json=data, auth=self._auth)
        return response

    def _delete(self, type, id):
        response = self._delete_request(type, id)
        if response.status_code == 204:
            logger.info(f"Deleted {type}: {id}")
        else:
            logger.info(f"Unable to delete {type}: {id}")


    def _create(self, type, name, values):
        if type == "users":
            data = create_user_payload(values)
        else:
            data = values
        response = self._create_request(type, data)
        if response.status_code == 200:
            logger.info(f"Created {type}: {name}")
        else:
            logger.info(f"Unable to create {type}: {name}")

    def _get_existing_objects(self, type):
        response = self._get_request(type)
        j = json.loads(response.text)
        if type == "groups":
            output = j["vXGroups"]
        if type == "users":
            output = j["vXUsers"]
        if type == "groupusers":
            output = j["vXGroupUsers"]
        return output

    def _sync(self, apply_objects, type):
        existing_objects = self._get_existing_objects(type)
        if type == "groups":
            for object in existing_objects:
                existing_name = object.get("name")
                matching = next(
                    (
                        appy_object
                        for appy_object in apply_objects
                        if appy_object.get("name") == existing_name
                    ),
                    None,
                )
                if existing_name in SYSTEM_GROUPS:
                    continue
                if not matching:
                    self._delete(type, object["id"])

        for object in apply_objects:
            apply_name = object.get("name")
            matching = next(
                (
                    existing_object
                    for existing_object in existing_objects
                    if existing_object.get("name") == apply_name
                ),
                None,
            )
            if not matching:
                self._create(type, apply_name, object)


    def _sync_memberships(self, apply_memberships):
        users = self._get_existing_objects("users")
        user_id_mapping = {user["name"]: user["id"] for user in users}
        existing_memberships = self._get_existing_objects("groupusers")
        for members in apply_memberships:
            user_names = members["users"].split(", ")
            user_ids = [
                user_id_mapping.get(user_name, user_name)
                for user_name in user_names
            ]
            members["users"] = user_ids

        existing_combinations = {
            (membership["name"], membership["userId"])
            for membership in existing_memberships
        }
        for apply_membership in apply_memberships:
            groupname = apply_membership["groupname"]
            for user_id in apply_membership["users"]:
                if (groupname, user_id) in existing_combinations:
                    # These already exist and need no further action, remove from dict.
                    existing_combinations.remove((groupname, user_id))
                else:
                    # Combination does not exist in Ranger, call create function
                    values = create_membership_payload(groupname, user_id)
                    self._create("groupusers", (groupname, user_id), values)

        for groupname, user_id in existing_combinations:
            delete_id = next(
                item["id"]
                for item in existing_memberships
                if item["name"] == groupname and item["userId"] == user_id
            )
            self._delete("groupusers", delete_id)


def create_user_payload(user):
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
    return {
            "name": groupname,
            "userId": user_id,
    }
