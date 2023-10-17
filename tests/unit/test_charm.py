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

GROUP_MANAGEMENT = """
    relation_1:
        users:
          - name: user1
            firstname: One
            lastname: User
            email: user1@canonical.com
        memberships:
          - groupname: commercial-systems
            users: [user1]
        groups:
          - name: commercial-systems
            description: commercial systems team
"""
MISSING_RELATION_CONFIG = """
        users:
          - name: user1
            firstname: One
            lastname: User
            email: user1@canonical.com
"""
INCORRECTLY_FORMATTED_CONFIG = """
            users:
          - name: user1
            firstname: One
            lastname: User
            email: user1@canonical.com
"""
MISSING_VALUE_CONFIG = """
    relation_1:
        users:
          - name: user1
            firstname: One
            lastname: User
            email: user1@canonical.com
"""


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

    def test_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness
        simulate_lifecycle(harness)

        # The plan is generated after pebble is ready.
        want_plan = {
            "services": {
                "ranger": {
                    "override": "replace",
                    "summary": "ranger server",
                    "command": "/tmp/entrypoint.sh",  # nosec
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
        self.assertEqual(got_plan, want_plan)

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
            "policy_manager_url": "http://ranger-k8s:6080"
        }

    def test_user_group_configuration(self):
        """The user-group-configuration paramerter is validated."""
        harness = self.harness
        simulate_lifecycle(harness)

        # Unable to parse configuration file.
        self.harness.update_config(
            {"user-group-configuration": f"{INCORRECTLY_FORMATTED_CONFIG}"}
        )
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus(
                "The configuration file is improperly formatted, unable to parse."
            ),
        )
        # Missing relation id in configuration file.
        self.harness.update_config(
            {"user-group-configuration": f"{MISSING_RELATION_CONFIG}"}
        )
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus(
                "User management configuration file must have relation keys."
            ),
        )

        # Missing value `groups` in configuration file.
        self.harness.update_config(
            {"user-group-configuration": f"{MISSING_VALUE_CONFIG}"}
        )
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus(
                "Missing 'groups' values in the configuration file."
            ),
        )

        # Correct configuration file.
        self.harness.update_config(
            {"user-group-configuration": f"{GROUP_MANAGEMENT}"}
        )
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_auth(self):
        """Ranger API authentication is created as expected."""
        harness = self.harness
        self.harness.update_config({"ranger-admin-password": "ubuntuR0cks!"})
        expected_auth = ("admin", "ubuntuR0cks!")
        auth = harness.charm.group_manager._auth
        self.assertEqual(auth, expected_auth)

    @mock.patch("charm.RangerGroupManager._get_existing_values")
    @mock.patch("charm.RangerGroupManager._delete_request")
    @mock.patch("charm.RangerGroupManager._create_request")
    def test_update_relation_data(
        self, _get_existing_values, _delete_request, _create_request
    ):
        """The user-group-configuration file is synced and relation data updated."""
        harness = self.harness
        simulate_lifecycle(harness)

        # Add Trino relation
        rel_id = harness.add_relation("policy", "trino-k8s")
        harness.add_relation_unit(rel_id, "trino-k8s/0")

        # ActiveStatus following check
        container = harness.model.unit.get_container("ranger")
        container.get_check = mock.Mock(status="up")
        container.get_check.return_value.status = CheckStatus.UP
        harness.charm.on.update_status.emit()

        # Update relation data with config
        self.harness.update_config(
            {"user-group-configuration": f"{GROUP_MANAGEMENT}"}
        )
        harness.charm.on.config_changed.emit()
        relation_data = self.harness.get_relation_data(rel_id, "ranger-k8s")
        assert relation_data.get("user-group-configuration") is not None


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
