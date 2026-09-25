#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scenario state builders and shared unit-test fixtures."""

import contextlib
import dataclasses
from unittest import mock

from ops.testing import Container, Exec, Model, Relation, Secret, State

from charm import ApiProbe
from literals import CREDENTIALS_SECRET_LABEL, MANAGED_USERS
from ranger_client import RangerAPIError, RangerAuthenticationError

RANGER = "ranger"
DATABASE_CONNECTION = {
    "endpoints": "myhost:5432",
    "username": "postgres_user",
    "password": "admin",  # nosec B105
}
CREDENTIALS_SECRET_CONTENT = {
    "admin": "RangerAdmin1",  # nosec B105
    "rangerusersync": "RangerUsersync1",  # nosec B105
    "keyadmin": "RangerKeyadmin1",  # nosec B105
    "rangertagsync": "RangerTagsync1",  # nosec B105
}
USERSYNC_CREDENTIALS_CONTENT = {
    "rangerusersync": "RangerUsersync1",  # nosec B105
}
LDAP_RELATION_CHANGED_DATA = {
    "admin_password": "huedw7uiedw7",  # nosec B105
    "base_dn": "dc=canonical,dc=dev,dc=com",
    "ldap_url": "ldap://comsys-openldap-k8s:389",
}
LDAP_CREDENTIALS_CONTENT = {
    "sync-ldap-bind-password": "admin",  # nosec B105
    "sync-ldap-bind-dn": "dc=canonical,dc=dev,dc=com",
}
LDAP_CONFIG_VALUES = {
    "sync-ldap-url": "ldap://config-openldap-k8s:389",
    "sync-ldap-search-base": "dc=canonical,dc=dev,dc=com",
    "sync-ldap-user-search-base": "dc=canonical,dc=dev,dc=com",
    "sync-group-search-base": "dc=canonical,dc=dev,dc=com",
}
POLICY_RELATION_DATA = {
    "name": "trino-service",
    "type": "trino",
    "jdbc.driverClassName": "io.trino.jdbc.TrinoDriver",
    "jdbc.url": "jdbc:trino://trino-k8s:8080",
}
USER_SECRET_CONTENT = {
    "username": "testuser",
    "password": "testpassword",  # nosec B105
}


class FakeRangerClient:
    """In-memory stand-in for RangerAPIClient that records calls."""

    def __init__(
        self,
        *,
        services=None,
        zones=None,
        roles=None,
        policies=None,
        failure=None,
        probe=ApiProbe.OK,
        passwords=None,
        users=None,
    ):
        """Construct a client with optional pre-existing Ranger resources.

        Args:
            services: Existing Ranger services.
            zones: Existing Ranger security zones.
            roles: Existing Ranger roles.
            policies: Existing Ranger policies.
            failure: Name of a method that raises RangerAPIError, or a mapping
                from method names to the number of failures to simulate.
            probe: Authentication result to emulate.
            passwords: Passwords Ranger accepts, keyed by user name. When given, it
                overrides the probe outcome for authentication.
            users: Existing Ranger internal users, keyed by user name.
        """
        self.services = {service.name: service for service in services or ()}
        self.zones = {zone.name: zone for zone in zones or ()}
        self.roles = {role.name: role for role in roles or ()}
        self.policies = {policy.name: policy for policy in policies or ()}
        self.failure = failure
        self._remaining_failures = dict(failure) if isinstance(failure, dict) else {}
        self.probe = probe
        self.passwords = dict(passwords) if passwords is not None else None
        self.users = users or {
            name: {"id": index + 1, "name": name, "firstName": name}
            for index, name in enumerate(MANAGED_USERS)
        }
        self.clients = []
        self.calls = []

    def _record(self, method, *args):
        """Record a call and raise the configured simulated failure."""
        self.calls.append((method, *args))
        if self.failure == method:
            raise RangerAPIError(f"{method} failed")
        if self._remaining_failures.get(method, 0):
            self._remaining_failures[method] -= 1
            raise RangerAPIError(f"{method} failed")

    def authenticate(self, timeout):
        """Emulate the read-only credentials probe."""
        self._record("authenticate", timeout)
        if self.passwords is not None:
            username, password = self.clients[-1][1]
            if self.passwords.get(username) != password:
                raise RangerAuthenticationError(401)
            return
        if self.probe is ApiProbe.REJECTED:
            raise RangerAuthenticationError(401)
        if self.probe is ApiProbe.UNREACHABLE:
            raise RangerAPIError("unreachable")

    def get_user(self, username):
        """Get an internal user by its login name."""
        self._record("get_user", username)
        if username not in self.users:
            raise RangerAPIError(f"Ranger returned no user named {username!r}.")
        return self.users[username]

    def change_own_password(self, user_id, login_id, old_password, new_password):
        """Change the password of the user the client authenticates as."""
        self._record("change_own_password", user_id, login_id, old_password, new_password)
        if self.passwords is not None:
            self.passwords[login_id] = new_password

    def set_user_password(self, user, new_password):
        """Set another internal user's password."""
        self._record("set_user_password", user["name"], new_password)
        if self.passwords is not None:
            self.passwords[user["name"]] = new_password

    def list_services(self):
        """List all in-memory services."""
        self._record("list_services")
        return list(self.services.values())

    def list_services_by_type(self, service_type):
        """List services matching a Ranger service type."""
        self._record("list_services_by_type", service_type)
        return [
            service
            for service in self.services.values()
            if getattr(service, "type", None) == service_type
        ]

    def get_service_by_name(self, name):
        """Get a service by its name."""
        self._record("get_service_by_name", name)
        return self.services.get(name)

    def create_service(self, service):
        """Store a newly created service."""
        self._record("create_service", service)
        self.services[service.name] = service
        return service

    def delete_service_by_id(self, service_id):
        """Delete a service by its numeric identifier."""
        self._record("delete_service_by_id", service_id)
        for name, service in self.services.items():
            if service.id == service_id:
                del self.services[name]
                return

    def list_zones(self):
        """List all in-memory security zones."""
        self._record("list_zones")
        return list(self.zones.values())

    def create_zone(self, zone):
        """Store a newly created security zone."""
        self._record("create_zone", zone)
        self.zones[zone.name] = zone
        return zone

    def list_roles(self):
        """List all in-memory roles."""
        self._record("list_roles")
        return list(self.roles.values())

    def create_role(self, role):
        """Store a newly created role."""
        self._record("create_role", role)
        self.roles[role.name] = role
        return role

    def list_service_policies(self, service_name):
        """List policies for a service."""
        self._record("list_service_policies", service_name)
        return [
            policy
            for policy in self.policies.values()
            if getattr(policy, "service", None) == service_name
        ]

    def list_policies(self, zone_name, service_name):
        """List policies for a security zone and service."""
        self._record("list_policies", zone_name, service_name)
        return [
            policy
            for policy in self.list_service_policies(service_name)
            if getattr(policy, "zoneName", None) == zone_name
        ]

    def create_policy(self, policy):
        """Store a newly created policy."""
        self._record("create_policy", policy)
        self.policies[policy.name] = policy
        return policy

    def delete_policy_by_id(self, policy_id):
        """Delete a policy by its numeric identifier."""
        self._record("delete_policy_by_id", policy_id)
        for name, policy in self.policies.items():
            if policy.id == policy_id:
                del self.policies[name]
                return


def ranger_container(**overrides):
    """Build a connectable Ranger workload container.

    Args:
        **overrides: Attributes to override on the Scenario container.

    Returns:
        A Ranger container with the exec calls used during reconciliation.
    """
    defaults = {
        "can_connect": True,
        "execs": {
            Exec(("keytool",), return_code=0),
        },
    }
    defaults.update(overrides)
    return Container(RANGER, **defaults)


def build_admin_state(
    *,
    leader=True,
    config=None,
    extra_relations=(),
    extra_secrets=(),
    database=DATABASE_CONNECTION,
    container=None,
    credentials=CREDENTIALS_SECRET_CONTENT,
) -> State:
    """Build an admin-function Scenario state.

    Args:
        leader: Whether the unit is the leader.
        config: Configuration overrides.
        extra_relations: Relations to append to the state.
        extra_secrets: Secrets to append to the state.
        database: Database remote app data, or None to omit the relation.
        container: Explicit Ranger container to use.
        credentials: Stored credentials content, or None to omit the secret.

    Returns:
        A state with charm-owned credentials and an optional ready database.
    """
    secrets = set(extra_secrets)
    if credentials is not None:
        secrets.add(Secret(credentials, label=CREDENTIALS_SECRET_LABEL, owner="app"))
    relations = set(extra_relations)
    if database is not None:
        relations.add(
            Relation(
                "database",
                remote_app_name="postgresql-k8s",
                remote_app_data=database,
            )
        )
    return State(
        leader=leader,
        model=Model(name="ranger-model"),
        config=dict(config or {}),
        containers={container or ranger_container()},
        relations=relations,
        secrets=secrets,
    )


def build_usersync_state(
    *,
    leader=True,
    config=None,
    extra_relations=(),
    extra_secrets=(),
    container=None,
) -> State:
    """Build a usersync-function Scenario state.

    Args:
        leader: Whether the unit is the leader.
        config: Configuration overrides.
        extra_relations: Relations to append to the state.
        extra_secrets: Secrets to append to the state.
        container: Explicit Ranger container to use.

    Returns:
        A state with LDAP relation data and a policy manager URL.
    """
    ldap_relation = Relation(
        "ldap",
        remote_app_name="comsys-openldap-k8s",
        remote_app_data=LDAP_RELATION_CHANGED_DATA,
    )
    usersync_secret = Secret(USERSYNC_CREDENTIALS_CONTENT)
    return build_admin_state(
        leader=leader,
        config={
            "charm-function": "usersync",
            "policy-mgr-url": "http://ranger-k8s:6080",
            "usersync-credentials": usersync_secret.id,
            **(config or {}),
        },
        extra_relations={ldap_relation, *extra_relations},
        extra_secrets={usersync_secret, *extra_secrets},
        database=None,
        container=container,
        credentials=None,
    )


def carry_forward(state):
    """Return a state suitable for a follow-up Scenario run.

    Scenario synthesizes check information from a rendered layer. Clearing it
    avoids passing attributes that do not match the layer definition back to
    Scenario's consistency checker.

    Args:
        state: The output state from a previous Context.run call.

    Returns:
        The state with generated Ranger check information removed.
    """
    container = dataclasses.replace(state.get_container(RANGER), check_infos=frozenset())
    return dataclasses.replace(state, containers={container})


def services(state):
    """Return the Ranger services from an output state.

    Args:
        state: The output state from a Context.run call.

    Returns:
        The rendered Pebble services mapping.
    """
    return state.get_container(RANGER).plan.to_dict()["services"]


def workload_path(state, ctx, path):
    """Return a path inside the mocked Ranger workload filesystem.

    Args:
        state: The output state from a Context.run call.
        ctx: The Scenario context used to run the hook.
        path: The absolute workload path.

    Returns:
        A filesystem path to the requested workload file.
    """
    return state.get_container(RANGER).get_filesystem(ctx) / path.lstrip("/")


@contextlib.contextmanager
def mock_ranger_api(probe=ApiProbe.OK, **behaviour):
    """Patch Ranger's API client with the requested probe outcome.

    Args:
        probe: Authentication result to emulate.
        **behaviour: Fake client resources and failure configuration.

    Yields:
        The mocked Ranger API client.
    """
    client = FakeRangerClient(probe=probe, **behaviour)

    def build_client(url, auth):
        """Record the credentials a caller built a client with.

        Args:
            url: The Ranger base URL.
            auth: The username and password tuple.

        Returns:
            The shared fake client.
        """
        client.clients.append((url, auth))
        return client

    with mock.patch("charm.RangerAPIClient", side_effect=build_client):
        yield client
