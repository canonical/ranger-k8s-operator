# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Charm unit tests."""

# pylint:disable=protected-access

import dataclasses
import json
import logging
import re
from unittest import TestCase, mock

import pytest
from ops import pebble, testing

from charm import RangerK8SCharm
from state import State

logger = logging.getLogger(__name__)

LDAP_RELATION_CHANGED_DATA = {
    "admin_password": "huedw7uiedw7",  # nosec
    "base_dn": "dc=canonical,dc=dev,dc=com",
    "ldap_url": "ldap://comsys-openldap-k8s:389",
}
USERSYNC_CONFIG_VALUES = {
    "sync-ldap-url": "ldap://config-openldap-k8s:389",
    "sync-ldap-bind-password": "admin",  # nosec
    "sync-ldap-search-base": "dc=canonical,dc=dev,dc=com",
    "sync-ldap-bind-dn": "dc=canonical,dc=dev,dc=com",
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
    "password": "testpassword",  # nosec
    "tls-ca": """-----BEGIN CERTIFICATE-----
    MIIC+DCCAeCgAwIBAgIJAKJdWfG2zRAQMA0GCSqGSIb3DQEBCwUAMIGPMQswCQYD
    -----END CERTIFICATE-----
    -----BEGIN CERTIFICATE-----
    AIBC+LCCAuCgAPIBAgIuAKJdWWG2zRAQMA0GFSqGSIP3DQEBCiUAMIGPMQswCQYC
    -----END CERTIFICATE-----""",
}


class MockService:
    """Defines functionality for the Ranger MockService."""

    def __init__(self, name, service_id):
        """Construct MockService object.

        Args:
            name: Ranger service name.
            service_id: Ranger service id.
        """
        self.name = name
        self.id = service_id


class MockRangerClient:
    """Defines functionality for the Ranger MockRangerClient."""

    def __init__(self):
        """Construct MockRangerClient object."""
        self.services = {}
        self.policies = {}

    def get_service_by_id(self, service_id):
        """Mock of get_service_by_id method.

        Args:
            service_id: Id of the service to fetch.

        Returns:
            service object
        """
        return self.services.get(service_id)

    def get_policies_in_service(self, service_name):
        """Mock of get_policies_in_service.

        Args:
            service_name: The service from which to get policies.

        Returns:
            policies from the service.
        """
        return self.policies.get(service_name, [])

    def delete_service_by_id(self, service_id):
        """Mock of delete_service_by_id.

        Args:
            service_id: Id of the service to fetch.
        """
        if service_id in self.services:
            del self.services[service_id]


RANGER = "ranger"

DATABASE_CONNECTION = {
    "dbname": "ranger-k8s_db",
    "host": "myhost",
    "port": "5432",
    "password": "admin",  # nosec
    "user": "postgres_user",
}

LDAP_STATE = {
    "sync_ldap_bind_password": "huedw7uiedw7",  # nosec
    "sync_ldap_bind_dn": "cn=admin,dc=canonical,dc=dev,dc=com",
    "sync_ldap_search_base": "dc=canonical,dc=dev,dc=com",
    "sync_ldap_user_search_base": "dc=canonical,dc=dev,dc=com",
    "sync_group_search_base": "dc=canonical,dc=dev,dc=com",
    "sync_ldap_url": "ldap://comsys-openldap-k8s:389",
}


@pytest.fixture
def ctx():
    """Return a Scenario context for the Ranger charm.

    Returns:
        A configured ops.testing.Context for RangerK8SCharm.
    """
    return testing.Context(RangerK8SCharm)


def _encode(data):
    """JSON-encode peer databag values the way the State store does.

    Args:
        data: mapping of state keys to Python values.

    Returns:
        Mapping of state keys to JSON-encoded string values.
    """
    return {key: json.dumps(value) for key, value in data.items()}


def _execs():
    """Return the workload exec mocks used during admin configuration.

    Returns:
        A set of Scenario Exec objects for the shell and keytool calls.
    """
    return {
        testing.Exec(("/bin/sh",), stdout="/usr/lib/jvm/java-21-openjdk-amd64/"),
        testing.Exec(("keytool",), return_code=0),
    }


def _container():
    """Return a connectable Ranger workload container.

    Returns:
        A Scenario Container for the ranger workload.
    """
    return testing.Container(RANGER, can_connect=True, execs=_execs())


def _peer(app_data=None):
    """Return the peer relation with JSON-encoded app databag state.

    Args:
        app_data: optional mapping of state keys to Python values.

    Returns:
        A Scenario PeerRelation for the ranger peer relation.
    """
    return testing.PeerRelation("peer", local_app_data=_encode(app_data or {}))


def _service_env(state_out):
    """Return the ranger service environment from an output State's plan.

    The randomly generated truststore password embedded in JAVA_OPTS is
    normalised so the value can be compared deterministically.

    Args:
        state_out: the State produced by ctx.run.

    Returns:
        The ranger service environment dict with a normalised JAVA_OPTS value.
    """
    env = state_out.get_container(RANGER).plan.to_dict()["services"]["ranger"]["environment"]
    if "JAVA_OPTS" in env:
        env["JAVA_OPTS"] = re.sub(r"=[^=]*$", "=***", env["JAVA_OPTS"])
    return env


def _service_dict(state_out):
    """Return the full ranger service definition from an output State's plan.

    Args:
        state_out: the State produced by ctx.run.

    Returns:
        The ranger service definition dict with a normalised JAVA_OPTS value.
    """
    service = state_out.get_container(RANGER).plan.to_dict()["services"]["ranger"]
    if "JAVA_OPTS" in service["environment"]:
        service["environment"]["JAVA_OPTS"] = re.sub(
            r"=[^=]*$", "=***", service["environment"]["JAVA_OPTS"]
        )
    return service


def _carry(state):
    """Reset a carried-forward container's check to a healthy UP status.

    Scenario derives a container's check status from its Pebble plan when a
    State is produced. Restoring the check to its healthy definition keeps the
    State consistent when reused in a subsequent `ctx.run`.

    Args:
        state: the State produced by a previous ctx.run.

    Returns:
        The State with the ranger container's check reset to UP.
    """
    container = state.get_container(RANGER)
    healthy = dataclasses.replace(
        container,
        check_infos={testing.CheckInfo("up", status=pebble.CheckStatus.UP)},
    )
    return dataclasses.replace(state, containers={healthy})


def test_initial_plan(ctx):
    """The initial pebble plan is empty."""
    state_out = ctx.run(
        ctx.on.install(),
        testing.State(leader=True, containers={_container()}),
    )
    assert state_out.get_container(RANGER).plan.to_dict() == {}


def test_suppress_debug_logs_configured(ctx):
    """Third-party loggers are set to WARNING when SUPPRESS_DEBUG_LOGS is enabled."""
    ctx.run(ctx.on.install(), testing.State(leader=True, containers={_container()}))
    assert logging.getLogger("apache_ranger").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_waiting_on_peer_relation_not_ready(ctx):
    """The charm is blocked without a peer relation."""
    container = _container()
    state_out = ctx.run(
        ctx.on.pebble_ready(container),
        testing.State(leader=True, containers={container}),
    )
    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.unit_status == testing.BlockedStatus("peer relation not ready")


def test_admin_ready(ctx):
    """The pebble plan is correctly generated when the charm is ready."""
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION})},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    want_service = {
        "override": "replace",
        "summary": "ranger admin",
        "command": "/home/ranger/scripts/ranger-admin-entrypoint.sh",  # nosec
        "startup": "enabled",
        "environment": {
            "DB_NAME": "ranger-k8s_db",
            "DB_HOST": "myhost",
            "DB_PORT": "5432",
            "DB_USER": "postgres_user",
            "DB_PWD": "admin",  # nosec
            "RANGER_ADMIN_PWD": "rangerR0cks!",  # nosec
            "JAVA_OPTS": "-Duser.timezone=UTC0 -Djavax.net.ssl.trustStorePassword=***",
            "OPENSEARCH_ENABLED": None,
            "OPENSEARCH_HOST": None,
            "OPENSEARCH_INDEX": None,
            "OPENSEARCH_PWD": None,
            "OPENSEARCH_PORT": None,
            "OPENSEARCH_USER": None,
            "RANGER_USERSYNC_PWD": "rangerR0cks!",  # nosec
        },
    }
    assert _service_dict(state_out) == want_service

    container_out = state_out.get_container(RANGER)
    assert container_out.service_statuses["ranger"] == pebble.ServiceStatus.ACTIVE
    assert state_out.unit_status == testing.MaintenanceStatus("replanning application")


def test_usersync_ready(ctx):
    """The pebble plan is correctly generated when the charm is ready."""
    ldap_rel = testing.Relation(
        "ldap",
        remote_app_name="comsys-openldap-k8s",
        remote_app_data=LDAP_RELATION_CHANGED_DATA,
    )
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        config={"charm-function": "usersync", "policy-mgr-url": "http://ranger-k8s:6080"},
        containers={_container()},
        relations={_peer(), ldap_rel},
    )
    state_out = ctx.run(ctx.on.relation_changed(ldap_rel), state_in)

    want_service = {
        "override": "replace",
        "summary": "ranger usersync",
        "command": "/home/ranger/scripts/ranger-usersync-entrypoint.sh",  # nosec
        "startup": "enabled",
        "environment": {
            "POLICY_MGR_URL": "http://ranger-k8s:6080",
            "RANGER_USERSYNC_PWD": "rangerR0cks!",  # nosec
            "SYNC_GROUP_USER_MAP_SYNC_ENABLED": True,
            "SYNC_GROUP_SEARCH_ENABLED": True,
            "SYNC_GROUP_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
            "SYNC_GROUP_OBJECT_CLASS": "posixGroup",
            "SYNC_INTERVAL": 3600,
            "SYNC_LDAP_BIND_DN": "cn=admin,dc=canonical,dc=dev,dc=com",
            "SYNC_LDAP_BIND_PASSWORD": "huedw7uiedw7",  # nosec
            "SYNC_LDAP_GROUP_SEARCH_SCOPE": "sub",
            "SYNC_LDAP_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
            "SYNC_LDAP_USER_SEARCH_FILTER": None,
            "SYNC_LDAP_URL": "ldap://comsys-openldap-k8s:389",
            "SYNC_LDAP_USER_GROUP_NAME_ATTRIBUTE": "memberOf",
            "SYNC_LDAP_USER_NAME_ATTRIBUTE": "uid",
            "SYNC_LDAP_USER_OBJECT_CLASS": "person",
            "SYNC_LDAP_USER_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
            "SYNC_LDAP_USER_SEARCH_SCOPE": "sub",
            "SYNC_GROUP_MEMBER_ATTRIBUTE_NAME": "memberUid",
            "SYNC_LDAP_DELTASYNC": True,
        },
    }
    assert _service_dict(state_out) == want_service
    assert (
        state_out.get_container(RANGER).service_statuses["ranger"] == pebble.ServiceStatus.ACTIVE
    )


def test_config_changed(ctx):
    """The pebble plan changes according to config changes."""
    state = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION})},
    )
    state = ctx.run(ctx.on.config_changed(), state)

    state = dataclasses.replace(state, config={"ranger-admin-password": "s3cure-pass"})  # nosec
    state_out = ctx.run(ctx.on.config_changed(), _carry(state))

    assert _service_env(state_out)["RANGER_ADMIN_PWD"] == "rangerR0cks!"  # nosec
    assert state_out.unit_status == testing.BlockedStatus(
        "value of 'ranger-admin-password' config cannot be changed after deployment. "
        "Value should be rangerR0cks!"
    )


def test_ingress_requirer_publishes_app_data(ctx):
    """The ingress requirer publishes correct app data on relation_changed."""
    ingress_rel = testing.Relation("ingress", remote_app_name="traefik-k8s")
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION}), ingress_rel},
    )
    state_out = ctx.run(ctx.on.relation_changed(ingress_rel), state_in)

    assert state_out.get_relation(ingress_rel.id).local_app_data == {
        "model": '"ranger-model"',
        "name": '"ranger-k8s"',
        "port": "6080",
        "strip-prefix": "true",
        "redirect-https": "true",
    }


def test_update_status_up(ctx):
    """The charm updates the unit status to active based on UP status."""
    state = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION})},
    )
    state = ctx.run(ctx.on.config_changed(), state)
    state_out = ctx.run(ctx.on.update_status(), _carry(state))

    assert state_out.unit_status == testing.ActiveStatus("Status check: UP")


def test_policy_on_relation_changed(ctx):
    """Test that the provider correctly handles service creation and relation update."""
    policy_rel = testing.Relation(
        "policy",
        remote_app_name="trino-k8s",
        remote_app_data=POLICY_RELATION_DATA,
    )
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION}), policy_rel},
    )
    with mock.patch("charm.RangerProvider._create_ranger_service") as mock_create:
        mock_create.return_value = (MockService(f"relation_{policy_rel.id}", policy_rel.id), True)
        state_out = ctx.run(ctx.on.relation_changed(policy_rel), state_in)

    assert state_out.get_relation(policy_rel.id).local_app_data == {
        "policy_manager_url": "http://ranger-k8s.ranger-model.svc.cluster.local:6080",
    }


def test_on_policy_relation_broken(ctx):
    """Test handling of broken policy relation and service deletion."""
    policy_rel = testing.Relation("policy", remote_app_name="trino-k8s")
    rel_id = policy_rel.id
    service_name = f"relation_{rel_id}"

    mock_ranger_client = MockRangerClient()
    mock_ranger_client.services[rel_id] = MockService(name=service_name, service_id=rel_id)
    mock_ranger_client.policies[service_name] = [
        {
            "name": "all - catalog",
            "policyItems": [{"users": ["custom_user"]}],
        }
    ]

    state_in = testing.State(
        leader=True,
        containers={_container()},
        relations={
            _peer(
                {
                    "database_connection": DATABASE_CONNECTION,
                    "services": {service_name: rel_id},
                }
            ),
            policy_rel,
        },
    )
    with mock.patch("charm.RangerProvider._create_ranger_client", return_value=mock_ranger_client):
        ctx.run(ctx.on.relation_broken(policy_rel), state_in)

    assert any(
        f"Service {service_name} has non-default policies defined. Deletion aborted."
        in line.message
        for line in ctx.juju_log
    )


def test_ldap_relation_changed(ctx):
    """The charm uses the configuration values from ldap relation."""
    ldap_rel = testing.Relation(
        "ldap",
        remote_app_name="comsys-openldap-k8s",
        remote_app_data=LDAP_RELATION_CHANGED_DATA,
    )
    state_in = testing.State(
        leader=True,
        config={"charm-function": "usersync", "policy-mgr-url": "http://ranger-k8s:6080"},
        containers={_container()},
        relations={_peer(), ldap_rel},
    )
    state_out = ctx.run(ctx.on.relation_changed(ldap_rel), state_in)

    env = _service_env(state_out)
    assert env["SYNC_LDAP_URL"] == "ldap://comsys-openldap-k8s:389"
    assert env["SYNC_GROUP_OBJECT_CLASS"] == "posixGroup"


def test_ldap_relation_broken(ctx):
    """The charm enters a blocked state if no LDAP parameters."""
    ldap_rel = testing.Relation("ldap", remote_app_name="comsys-openldap-k8s")
    state_in = testing.State(
        leader=True,
        config={"charm-function": "usersync"},
        containers={_container()},
        relations={_peer({"ldap": LDAP_STATE}), ldap_rel},
    )
    state_out = ctx.run(ctx.on.relation_broken(ldap_rel), state_in)

    assert state_out.unit_status == testing.BlockedStatus(
        "Add an LDAP relation or update config values."
    )


def test_ldap_config_updated(ctx):
    """The charm uses the configuration values from config relation."""
    state_in = testing.State(
        leader=True,
        config={
            "charm-function": "usersync",
            "policy-mgr-url": "http://ranger-k8s:6080",
            **USERSYNC_CONFIG_VALUES,
        },
        containers={_container()},
        relations={_peer()},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert _service_env(state_out)["SYNC_LDAP_URL"] == "ldap://config-openldap-k8s:389"


def test_on_opensearch_index_created(ctx):
    """Test handling of opensearch relation changed events."""
    user_secret = testing.Secret({"username": "testuser", "password": "testpassword"})  # nosec
    tls_secret = testing.Secret({"tls-ca": USER_SECRET_CONTENT["tls-ca"]})
    opensearch_rel = testing.Relation(
        "opensearch",
        remote_app_name="opensearch-app",
        remote_app_data={
            "secret-user": user_secret.id,
            "secret-tls": tls_secret.id,
            "endpoints": "opensearch-host:9200",
            "index": "ranger_audits",
        },
    )
    state_in = testing.State(
        leader=True,
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION}), opensearch_rel},
        secrets={user_secret, tls_secret},
    )
    with mock.patch("charm.OpensearchRelationHandler.add_opensearch_schema"):
        state_out = ctx.run(ctx.on.relation_changed(opensearch_rel), state_in)

    assert state_out.unit_status == testing.MaintenanceStatus("replanning application")
    assert _service_env(state_out)["OPENSEARCH_HOST"] == "opensearch-host"


def test_on_opensearch_relation_broken(ctx):
    """Test handling of broken relations with opensearch."""
    opensearch_rel = testing.Relation("opensearch", remote_app_name="opensearch-app")
    state_in = testing.State(
        leader=True,
        containers={_container()},
        relations={
            _peer(
                {
                    "database_connection": DATABASE_CONNECTION,
                    "opensearch": {
                        "is_enabled": True,
                        "host": "opensearch-host",
                        "port": "9200",
                        "index": "ranger_audits",
                        "username": "testuser",
                        "password": "testpassword",  # nosec
                    },
                    "truststore_pwd": "test-truststore-pwd",  # nosec
                    "opensearch_certificate": "cert-value",
                }
            ),
            opensearch_rel,
        },
    )
    state_out = ctx.run(ctx.on.relation_broken(opensearch_rel), state_in)

    assert _service_env(state_out)["OPENSEARCH_ENABLED"] is False


def test_usersync_blocked_without_policy_mgr_url(ctx):
    """Usersync function blocks if policy-mgr-url is not configured."""
    ldap_rel = testing.Relation(
        "ldap",
        remote_app_name="comsys-openldap-k8s",
        remote_app_data=LDAP_RELATION_CHANGED_DATA,
    )
    state_in = testing.State(
        leader=True,
        config={"charm-function": "usersync"},
        containers={_container()},
        relations={_peer(), ldap_rel},
    )
    state_out = ctx.run(ctx.on.relation_changed(ldap_rel), state_in)

    assert state_out.unit_status == testing.BlockedStatus(
        "Missing required configuration: set 'policy-mgr-url' for usersync function."
    )


def test_deprecated_config_no_error(ctx):
    """Setting deprecated config options does not raise a validation error."""
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        config={"external-hostname": "my-hostname", "tls-secret-name": "my-tls-secret"},
        containers={_container()},
        relations={_peer({"database_connection": DATABASE_CONNECTION})},
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status != testing.BlockedStatus("external-hostname")
    assert state_out.unit_status != testing.BlockedStatus("tls-secret-name")


def test_policy_mgr_url_from_ingress(ctx):
    """Policy databag uses the normalized ingress URL when an ingress relation is ready."""
    ingress_rel = testing.Relation(
        "ingress",
        remote_app_name="traefik-k8s",
        remote_app_data={"ingress": '{"url": "http://ranger-k8s.example.com/"}'},
    )
    policy_rel = testing.Relation(
        "policy",
        remote_app_name="trino-k8s",
        remote_app_data=POLICY_RELATION_DATA,
    )
    state_in = testing.State(
        leader=True,
        model=testing.Model(name="ranger-model"),
        containers={_container()},
        relations={
            _peer({"database_connection": DATABASE_CONNECTION}),
            ingress_rel,
            policy_rel,
        },
    )
    with mock.patch("charm.RangerProvider._create_ranger_service") as mock_create:
        mock_create.return_value = (MockService(f"relation_{policy_rel.id}", policy_rel.id), True)
        state_out = ctx.run(ctx.on.relation_changed(ingress_rel), state_in)

    assert state_out.get_relation(policy_rel.id).local_app_data == {
        "policy_manager_url": "http://ranger-k8s.example.com:80",
    }


class TestState(TestCase):
    """Unit tests for state.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def test_get(self):
        """It is possible to retrieve attributes from the state."""
        state = make_state({"foo": json.dumps("bar")})
        self.assertEqual(state.foo, "bar")
        self.assertIsNone(state.bad)

    def test_set(self):
        """It is possible to set attributes in the state."""
        data = {"foo": json.dumps("bar")}
        state = make_state(data)
        state.foo = 42
        state.list = [1, 2, 3]
        self.assertEqual(state.foo, 42)
        self.assertEqual(state.list, [1, 2, 3])
        self.assertEqual(data, {"foo": "42", "list": "[1, 2, 3]"})

    def test_del(self):
        """It is possible to unset attributes in the state."""
        data = {"foo": json.dumps("bar"), "answer": json.dumps(42)}
        state = make_state(data)
        del state.foo
        self.assertIsNone(state.foo)
        self.assertEqual(data, {"answer": "42"})
        # Deleting a name that is not set does not error.
        del state.foo

    def test_is_ready(self):
        """The state is not ready when it is not possible to get relations."""
        state = make_state({})
        self.assertTrue(state.is_ready())

        state = State("myapp", lambda: None)
        self.assertFalse(state.is_ready())


def make_state(data):
    """Create state object.

    Args:
        data: Data to be included in state.

    Returns:
        State object with data.
    """
    app = "myapp"
    rel = type("Rel", (), {"data": {app: data}})()
    return State(app, lambda: rel)
