# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.


"""Charm unit tests."""

# pylint:disable=protected-access

import json
import logging
from unittest import TestCase, mock

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import CheckStatus
from ops.testing import Harness

from charm import RangerK8SCharm
from state import State

logger = logging.getLogger(__name__)


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
        simulate_lifecycle(harness)

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
                        "JAVA_OPTS": "-Duser.timezone=UTC0",
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

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_usersync_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness
        simulate_lifecycle(harness)
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
                        "POLICY_MGR_URL": "http://ranger-admin:6080",
                        "RANGER_USERSYNC_PASSWORD": "rangerR0cks!",
                        "SYNC_GROUP_USER_MAP_SYNC_ENABLED": True,
                        "SYNC_GROUP_SEARCH_ENABLED": True,
                        "SYNC_GROUP_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
                        "SYNC_GROUP_OBJECT_CLASS": "posixGroup",
                        "SYNC_INTERVAL": 3600000,
                        "SYNC_LDAP_BIND_DN": "cn=admin,dc=canonical,dc=dev,dc=com",
                        "SYNC_LDAP_BIND_PASSWORD": "admin",
                        "SYNC_LDAP_GROUP_SEARCH_SCOPE": "sub",
                        "SYNC_LDAP_SEARCH_BASE": "dc=canonical,dc=dev,dc=com",
                        "SYNC_LDAP_USER_SEARCH_FILTER": None,
                        "SYNC_LDAP_URL": "ldap://openldap-k8s:3893",
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
        simulate_lifecycle(harness)

        # Update the config.
        self.harness.update_config({"ranger-admin-password": "secure-pass"})

        # The new plan reflects the change.
        want_admin_password = "secure-pass"  # nosec
        got_admin_password = harness.get_container_pebble_plan(
            "ranger"
        ).to_dict()["services"]["ranger"]["environment"]["RANGER_ADMIN_PWD"]

        self.assertEqual(got_admin_password, want_admin_password)

        # The ActiveStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_ingress(self):
        """The charm relates correctly to the nginx ingress charm."""
        harness = self.harness

        simulate_lifecycle(harness)

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

        simulate_lifecycle(harness)

        container = harness.model.unit.get_container("ranger")
        container.get_check = mock.Mock(status="up")
        container.get_check.return_value.status = CheckStatus.UP
        harness.charm.on.update_status.emit()

        self.assertEqual(
            harness.model.unit.status, ActiveStatus("Status check: UP")
        )

    @mock.patch("charm.RangerProvider._create_ranger_service")
    def test_provider(self, _create_ranger_service):
        """The charm relates correctly to the nginx ingress charm."""
        harness = self.harness
        simulate_lifecycle(harness)

        rel_id = harness.add_relation("policy", "trino-k8s")
        harness.add_relation_unit(rel_id, "trino-k8s/0")

        event = make_policy_relation_changed_event(rel_id)
        harness.charm.provider._on_relation_changed(event)

        relation_data = self.harness.get_relation_data(rel_id, "ranger-k8s")
        assert relation_data == {
            "policy_manager_url": "http://ranger-k8s:6080",
            "service_name": "trino-service",
        }


def simulate_lifecycle(harness):
    """Simulate a healthy charm life-cycle.

    Args:
        harness: ops.testing.Harness object used to simulate charm lifecycle.
    """
    # Simulate peer relation readiness.
    harness.add_relation("peer", "ranger")

    # Simulate pebble readiness.
    container = harness.model.unit.get_container("ranger")
    harness.charm.on.ranger_pebble_ready.emit(container)

    # Simulate database readiness.
    event = make_database_changed_event()
    harness.charm.postgres_relation_handler._on_database_changed(event)


def make_policy_relation_changed_event(rel_id):
    """Create and return a mock database changed event.

    The event is generated by the relation with ranger-k8s

    Args:
        rel_id: the relation id.

    Returns:
        Event dict.
    """
    return type(
        "Event",
        (),
        {
            "app": "trino-k8s",
            "relation": type(
                "Relation",
                (),
                {
                    "data": {
                        "trino-k8s": {
                            "name": "trino-service",
                            "type": "trino",
                            "jdbc.driverClassName": "io.trino.jdbc.TrinoDriver",
                            "jdbc.url": "jdbc:trino://trino-k8s:8080",
                        }
                    },
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
