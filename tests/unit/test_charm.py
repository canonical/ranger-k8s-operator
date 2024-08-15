# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Charm unit tests."""

# pylint:disable=protected-access

import json
import logging
import re
from unittest import TestCase, mock

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import CheckStatus
from ops.testing import Harness

from charm import RangerK8SCharm
from state import State

logger = logging.getLogger(__name__)

LDAP_RELATION_CHANGED_DATA = {
    "admin_password": "huedw7uiedw7",
    "base_dn": "dc=canonical,dc=dev,dc=com",
    "ldap_url": "ldap://comsys-openldap-k8s:389",
}
LDAP_RELATION_BROKEN_DATA: dict = {"comsys-openldap-k8s": {}}
USERSYNC_CONFIG_VALUES = {
    "sync-ldap-url": "ldap://config-openldap-k8s:389",
    "sync-ldap-bind-password": "admin",
    "sync-ldap-search-base": "dc=canonical,dc=dev,dc=com",
    "sync-ldap-bind-dn": "dc=canonical,dc=dev,dc=com",
    "sync-ldap-user-search-base": "dc=canonical,dc=dev,dc=com",
    "sync-group-search-base": "dc=canonical,dc=dev,dc=com",
}

OPENSEARCH_RELATION_CHANGED_DATA = {
    "user-secret": "thiahuid",
    "tls-secret": "relation_1",
    "endpoints": "opensearch-host:port",
}
OPENSEARCH_RELATION_BROKEN_DATA: dict = {"opensearch": {}}
POLICY_RELATION_DATA = {
    "name": "trino-service",
    "type": "trino",
    "jdbc.driverClassName": "io.trino.jdbc.TrinoDriver",
    "jdbc.url": "jdbc:trino://trino-k8s:8080",
}
USER_SECRET_CONTENT = {
    "username": "testuser",
    "password": "testpassword",
    "tls-ca": """-----BEGIN CERTIFICATE-----
    MIIC+DCCAeCgAwIBAgIJAKJdWfG2zRAQMA0GCSqGSIb3DQEBCwUAMIGPMQswCQYD
    -----END CERTIFICATE-----
    -----BEGIN CERTIFICATE-----
    AIBC+LCCAuCgAPIBAgIuAKJdWWG2zRAQMA0GFSqGSIP3DQEBCiUAMIGPMQswCQYC
    -----END CERTIFICATE-----""",
}

JAVA_HOME = "/usr/lib/jvm/java-11-openjdk-amd64"


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


class TestCharm(TestCase):
    """Unit tests.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def setUp(self):
        """Set up for the unit tests."""
        self.harness = Harness(RangerK8SCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_can_connect("ranger", True)
        self.harness.set_leader(True)
        self.harness.set_model_name("ranger-model")
        self.harness.add_network("10.0.0.10", endpoint="peer")
        self.harness.begin()
        logging.info("setup complete")

    def test_initial_plan(self):
        """The initial pebble plan is empty."""
        harness = self.harness
        initial_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(initial_plan, {})

    def test_waiting_on_peer_relation_not_ready(self):
        """The charm is blocked without a peer relation."""
        harness = self.harness

        # Simulate pebble readiness.
        container = harness.model.unit.get_container("ranger")
        harness.charm.on.ranger_pebble_ready.emit(container)

        # No plans are set yet.
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(got_plan, {})

        # The BlockStatus is set with a message.
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus("peer relation not ready"),
        )

    def test_admin_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness
        simulate_admin_lifecycle(harness)

        # The plan is generated after pebble is ready.
        want_plan = {
            "services": {
                "ranger": {
                    "override": "replace",
                    "summary": "ranger admin",
                    "command": "/home/ranger/scripts/ranger-admin-entrypoint.sh",  # nosec
                    "startup": "enabled",
                    "environment": {
                        "DB_NAME": "ranger-k8s_db",
                        "DB_HOST": "myhost",
                        "DB_PORT": "5432",
                        "DB_USER": "postgres_user",
                        "DB_PWD": "admin",
                        "RANGER_ADMIN_PWD": "rangerR0cks!",
                        "JAVA_OPTS": "-Duser.timezone=UTC0 -Djavax.net.ssl.trustStorePassword=***",
                        "OPENSEARCH_ENABLED": None,
                        "OPENSEARCH_HOST": None,
                        "OPENSEARCH_INDEX": None,
                        "OPENSEARCH_PWD": None,
                        "OPENSEARCH_PORT": None,
                        "OPENSEARCH_USER": None,
                        "RANGER_USERSYNC_PWD": "rangerR0cks!",
                    },
                }
            },
        }
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        java_ops = got_plan["services"]["ranger"]["environment"]["JAVA_OPTS"]
        got_plan["services"]["ranger"]["environment"]["JAVA_OPTS"] = re.sub(
            r"=[^=]*$", "=***", java_ops
        )
        self.assertEqual(got_plan["services"], want_plan["services"])

        # The service was started.
        service = harness.model.unit.get_container("ranger").get_service(
            "ranger"
        )
        self.assertTrue(service.is_running())

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_usersync_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness
        simulate_usersync_lifecycle(harness)
        harness.update_config({"charm-function": "usersync"})

        # The plan is generated after pebble is ready.
        want_plan = {
            "services": {
                "ranger": {
                    "override": "replace",
                    "summary": "ranger usersync",
                    "command": "/home/ranger/scripts/ranger-usersync-entrypoint.sh",  # nosec
                    "startup": "enabled",
                    "environment": {
                        "POLICY_MGR_URL": "http://ranger-k8s:6080",
                        "RANGER_USERSYNC_PWD": "rangerR0cks!",
                        "SYNC_GROUP_USER_MAP_SYNC_ENABLED": True,
                        "SYNC_GROUP_SEARCH_ENABLED": True,
                        "SYNC_GROUP_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
                        "SYNC_GROUP_OBJECT_CLASS": "posixGroup",
                        "SYNC_INTERVAL": 3600000,
                        "SYNC_LDAP_BIND_DN": "cn=admin,dc=canonical,dc=dev,dc=com",
                        "SYNC_LDAP_BIND_PASSWORD": "huedw7uiedw7",
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
            },
        }
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(got_plan["services"], want_plan["services"])

        # The service was started.
        service = harness.model.unit.get_container("ranger").get_service(
            "ranger"
        )
        self.assertTrue(service.is_running())

    def test_config_changed(self):
        """The pebble plan changes according to config changes."""
        harness = self.harness
        simulate_admin_lifecycle(harness)

        # Update the config.
        self.harness.update_config({"ranger-admin-password": "s3cure-pass"})

        # The new plan reflects the change.
        want_admin_password = "rangerR0cks!"  # nosec
        got_admin_password = harness.get_container_pebble_plan(
            "ranger"
        ).to_dict()["services"]["ranger"]["environment"]["RANGER_ADMIN_PWD"]

        self.assertEqual(got_admin_password, want_admin_password)

        # The Maintenance Status is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus(
                "value of 'ranger-admin-password' config cannot be changed after deployment. "
                "Value should be rangerR0cks!"
            ),
        )

    def test_ingress(self):
        """The charm relates correctly to the nginx ingress charm."""
        harness = self.harness

        simulate_admin_lifecycle(harness)

        nginx_route_relation_id = harness.add_relation(
            "nginx-route", "ingress"
        )
        harness.charm._require_nginx_route()

        assert harness.get_relation_data(
            nginx_route_relation_id, harness.charm.app
        ) == {
            "service-namespace": harness.charm.model.name,
            "service-hostname": harness.charm.app.name,
            "service-name": harness.charm.app.name,
            "service-port": "6080",
            "backend-protocol": "HTTP",
            "tls-secret-name": "ranger-tls",
        }

    def test_update_status_up(self):
        """The charm updates the unit status to active based on UP status."""
        harness = self.harness

        simulate_admin_lifecycle(harness)

        container = harness.model.unit.get_container("ranger")
        container.get_check = mock.Mock(status="up")
        container.get_check.return_value.status = CheckStatus.UP
        harness.charm.on.update_status.emit()

        self.assertEqual(
            harness.model.unit.status, ActiveStatus("Status check: UP")
        )

    @mock.patch("charm.RangerProvider._create_ranger_service")
    def policy_relation_setup(self, mock_create_ranger_service):
        """Setup the policy relation.

        Args:
            mock_create_ranger_service: mock create_ranger_service method.

        Returns:
            harness: harness test object.
            rel_id: The Trino relation Id.
        """
        harness = self.harness
        simulate_admin_lifecycle(harness)

        rel_id = harness.add_relation("policy", "trino-k8s")
        harness.add_relation_unit(rel_id, "trino-k8s/0")

        data = POLICY_RELATION_DATA
        event = make_relation_event(rel_id, "trino-k8s", data)

        mock_create_ranger_service.return_value = (
            MockService(f"relation_{rel_id}", rel_id),
            True,
        )
        harness.charm.provider._on_relation_changed(event)
        return harness, rel_id

    def test_policy_on_relation_changed(self):
        """Test that the provider correctly handles service creation and relation update."""
        harness, rel_id = self.policy_relation_setup()
        relation_data = harness.get_relation_data(rel_id, "ranger-k8s")
        expected_data = {
            "policy_manager_url": "http://ranger-k8s:6080",
            "service_name": "trino-service",
        }
        self.assertEqual(relation_data, expected_data)

    @mock.patch("charm.RangerProvider._create_ranger_service")
    @mock.patch("charm.RangerProvider._create_ranger_client")
    def test_on_policy_relation_broken(
        self, mock_create_ranger_client, mock_create_ranger_service
    ):
        """Test handling of broken policy relation and service deletion."""
        harness, rel_id = self.policy_relation_setup()

        # Set up mock Ranger client
        mock_ranger_client = MockRangerClient()
        mock_create_ranger_client.return_value = mock_ranger_client

        # Set up a mock service
        service_name = f"relation_{rel_id}"
        mock_service = MockService(name=service_name, service_id=rel_id)
        mock_ranger_client.services[rel_id] = mock_service

        # Define custom policies for the service
        mock_ranger_client.policies[service_name] = [
            {
                "name": "all - catalog",
                "policyItems": [{"users": ["custom_user"]}],
            }
        ]

        # Simulate relation broken event
        event = make_relation_event(rel_id, "trino-k8s", {})

        with self.assertLogs(level="WARNING") as log:
            harness.charm.provider._on_relation_broken(event)

        # Check that the specific warning message is in the logs
        self.assertTrue(
            any(
                "Service relation_1 has non-default policies defined. Deletion aborted."
                in message
                for message in log.output
            )
        )

    def test_ldap_relation_changed(self):
        """The charm uses the configuration values from ldap relation."""
        harness = self.harness
        simulate_usersync_lifecycle(harness)

        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(
            got_plan["services"]["ranger"]["environment"]["SYNC_LDAP_URL"],
            "ldap://comsys-openldap-k8s:389",
        )
        self.assertEqual(
            got_plan["services"]["ranger"]["environment"][
                "SYNC_GROUP_OBJECT_CLASS"
            ],
            "posixGroup",
        )

    def test_ldap_relation_broken(self):
        """The charm enters a blocked state if no LDAP parameters."""
        harness = self.harness
        rel_id = simulate_usersync_lifecycle(harness)

        data = LDAP_RELATION_BROKEN_DATA
        event = make_relation_event(rel_id, "comsys-openldap-k8s", data)
        harness.charm.ldap._on_relation_broken(event)
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus("Add an LDAP relation or update config values."),
        )

    def test_ldap_config_updated(self):
        """The charm uses the configuration values from config relation."""
        harness = self.harness
        self.test_ldap_relation_broken()
        harness.update_config(USERSYNC_CONFIG_VALUES)
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(
            got_plan["services"]["ranger"]["environment"]["SYNC_LDAP_URL"],
            "ldap://config-openldap-k8s:389",
        )

    @mock.patch("charm.OpensearchRelationHandler.get_secret_content")
    @mock.patch("charm.OpensearchRelationHandler.add_opensearch_schema")
    def opensearch_setup(
        self,
        mock_add_opensearch_schema,
        mock_get_secret_content,
        harness,
        data,
    ):
        """Common setup for Openseatch relation changed and broken tests.

        Args:
            mock_add_opensearch_schema: the mocked method for schema setup.
            mock_get_secret_content: the mocked method for accessing juju secrets.
            harness: ops.testing.Harness object used to simulate charm lifecycle.
            data: the opensearch relation data.

        Returns:
            rel_id: the opensearch relation id.
        """
        mock_get_secret_content.return_value = USER_SECRET_CONTENT

        simulate_admin_lifecycle(harness)
        rel_id = harness.add_relation("opensearch", "opensearch-app")
        harness.add_relation_unit(rel_id, "opensearch-app/0")

        event = make_relation_event(rel_id, "opensearch", data)
        harness.charm.opensearch_relation_handler._on_index_created(event)
        return rel_id

    def test_on_opensearch_index_created(self):
        """Test handling of opensearch relation changed events."""
        harness = self.harness
        self.opensearch_setup(
            harness=harness, data=OPENSEARCH_RELATION_CHANGED_DATA
        )

        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(
            got_plan["services"]["ranger"]["environment"]["OPENSEARCH_HOST"],
            "opensearch-host",
        )

    def test_on_opensearch_relation_broken(self):
        """Test handling of broken relations with opensearch."""
        harness = self.harness
        rel_id = self.opensearch_setup(
            harness=harness, data=OPENSEARCH_RELATION_CHANGED_DATA
        )
        data = OPENSEARCH_RELATION_BROKEN_DATA
        event = make_relation_event(rel_id, "opensearch", data)
        self.harness.charm.opensearch_relation_handler._on_relation_broken(
            event
        )
        got_plan = harness.get_container_pebble_plan("ranger").to_dict()
        self.assertEqual(
            got_plan["services"]["ranger"]["environment"][
                "OPENSEARCH_ENABLED"
            ],
            False,
        )


def simulate_usersync_lifecycle(harness):
    """Simulate a healthy charm life-cycle.

    Args:
        harness: ops.testing.Harness object used to simulate charm lifecycle.

    Returns:
        rel_id: ldap relation id to be used for subsequent testing.
    """
    # Simulate peer relation readiness.
    harness.add_relation("peer", "ranger")

    # Simulate pebble readiness.
    container = harness.model.unit.get_container("ranger")
    harness.charm.on.ranger_pebble_ready.emit(container)

    harness.update_config({"charm-function": "usersync"})

    # Simulate LDAP readiness.
    rel_id = harness.add_relation("ldap", "comsys-openldap-k8s")
    harness.add_relation_unit(rel_id, "comsys-openldap-k8s/0")
    event = make_relation_event(
        rel_id, "comsys-openldap-k8s", LDAP_RELATION_CHANGED_DATA
    )
    harness.charm.ldap._on_relation_changed(event)
    return rel_id


def simulate_admin_lifecycle(harness):
    """Simulate a healthy charm life-cycle.

    Args:
        harness: ops.testing.Harness object used to simulate charm lifecycle.
    """
    # Simulate peer relation readiness.
    harness.add_relation("peer", "ranger")

    # Simulate pebble readiness.
    container = harness.model.unit.get_container("ranger")
    harness.charm.on.ranger_pebble_ready.emit(container)

    harness.handle_exec("ranger", [f"{JAVA_HOME}/bin/keytool"], result=0)

    # Simulate database readiness.
    event = make_database_changed_event()
    harness.charm.postgres_relation_handler._on_database_changed(event)


def make_relation_event(rel_id, app_name, data):
    """Create and return a mock relation event.

    Args:
        rel_id: The relation id.
        app_name: The name of the application.
        data: The relation data.

    Returns:
        Event dict.
    """
    return type(
        "Event",
        (),
        {
            "app": app_name,
            "relation": type(
                "Relation",
                (),
                {
                    "data": {app_name: data},
                    "id": rel_id,
                },
            ),
        },
    )


def make_database_changed_event():
    """Create and return a mock database changed event.

        The event is generated by the relation with postgresql_db

    Returns:
        Event dict.
    """
    return type(
        "Event",
        (),
        {
            "endpoints": "myhost:5432",
            "username": "postgres_user",
            "password": "admin",
            "database": "ranger-k8s_db",
            "relation": type("Relation", (), {"name": "postgresql_db"}),
        },
    )


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
