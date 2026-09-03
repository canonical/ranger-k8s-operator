#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scenario state builders and shared unit-test fixtures."""

import contextlib
import dataclasses
from unittest import mock

from ops.testing import Container, Exec, Model, Relation, Secret, State

from charm import ApiProbe
from ranger_client import RangerAPIError, RangerAuthenticationError

RANGER = "ranger"
DATABASE_CONNECTION = {
    "endpoints": "myhost:5432",
    "username": "postgres_user",
    "password": "admin",  # nosec B105
}
SYSTEM_USERS_SECRET_CONTENT = {
    "admin": "RangerAdmin1",  # nosec B105
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
            Exec(("/bin/sh", "-c", "echo $JAVA_HOME"), stdout="/usr/lib/jvm/java-21-openjdk/"),
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
) -> State:
    """Build an admin-function Scenario state.

    Args:
        leader: Whether the unit is the leader.
        config: Configuration overrides.
        extra_relations: Relations to append to the state.
        extra_secrets: Secrets to append to the state.
        database: Database remote app data, or None to omit the relation.
        container: Explicit Ranger container to use.

    Returns:
        A state with a valid system-users secret and optional ready database.
    """
    system_users = Secret(SYSTEM_USERS_SECRET_CONTENT)
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
        config={"system-users": system_users.id, **(config or {})},
        containers={container or ranger_container()},
        relations=relations,
        secrets={system_users, *extra_secrets},
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
    return build_admin_state(
        leader=leader,
        config={
            "charm-function": "usersync",
            "policy-mgr-url": "http://ranger-k8s:6080",
            **(config or {}),
        },
        extra_relations={ldap_relation, *extra_relations},
        extra_secrets=extra_secrets,
        database=None,
        container=container,
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
        **behaviour: Mock client methods and values to configure.

    Yields:
        The mocked Ranger API client.
    """
    client = mock.MagicMock()
    client.list_services.return_value = []
    client.list_services_by_type.return_value = []
    if probe is ApiProbe.REJECTED:
        client.authenticate.side_effect = RangerAuthenticationError(401)
    elif probe is ApiProbe.UNREACHABLE:
        client.authenticate.side_effect = RangerAPIError("unreachable")
    for name, value in behaviour.items():
        setattr(client, name, value)
    with mock.patch("charm.RangerAPIClient", return_value=client):
        yield client
