# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Trino catalog reconciler."""

# pylint:disable=protected-access

import logging
from unittest import TestCase, mock

from apache_ranger.model.ranger_policy import RangerPolicy
from apache_ranger.model.ranger_role import RangerRole
from apache_ranger.model.ranger_security_zone import RangerSecurityZone

from ranger_client import RangerAPIError
from reconcile import (
    TrinoCatalogReconciler,
    _build_ddl_policy,
    _build_is_policy,
    _build_ro_policy,
    _build_rw_policy,
    _catalogs_to_zones,
    _default_policy_names,
    _role_names,
)

logger = logging.getLogger(__name__)

SERVICE_NAME = "trino-service"


class TestCatalogsToZones(TestCase):
    """Tests for the catalog-to-zone mapping function."""

    def test_base_catalog_only(self):
        """A single base catalog maps to one zone."""
        catalogs = [{"name": "marketing"}]
        self.assertEqual(_catalogs_to_zones(catalogs), {"marketing"})

    def test_developer_catalog_only(self):
        """A developer catalog maps to the base zone name."""
        catalogs = [{"name": "marketing_developer"}]
        self.assertEqual(_catalogs_to_zones(catalogs), {"marketing"})

    def test_base_and_developer(self):
        """Both base and developer catalogs map to one zone."""
        catalogs = [
            {"name": "marketing"},
            {"name": "marketing_developer"},
        ]
        self.assertEqual(_catalogs_to_zones(catalogs), {"marketing"})

    def test_multiple_zones(self):
        """Multiple catalog pairs produce multiple zones."""
        catalogs = [
            {"name": "marketing"},
            {"name": "marketing_developer"},
            {"name": "sales"},
            {"name": "finance_developer"},
        ]
        self.assertEqual(
            _catalogs_to_zones(catalogs),
            {"marketing", "sales", "finance"},
        )

    def test_empty_catalogs(self):
        """No catalogs produce no zones."""
        self.assertEqual(_catalogs_to_zones([]), set())


class TestHelpers(TestCase):
    """Tests for helper functions."""

    def test_role_names(self):
        """Role names follow the expected pattern."""
        names = _role_names("marketing")
        self.assertEqual(
            names,
            [
                "marketing-viewer",
                "marketing-editor",
                "marketing-admin",
                "marketing-auditor",
            ],
        )

    def test_default_policy_names(self):
        """Default policy names follow the expected pattern."""
        names = _default_policy_names("marketing")
        self.assertEqual(
            names,
            {
                "default - ro - marketing",
                "default - rw - marketing",
                "default - ddl - marketing",
                "default - is - marketing",
            },
        )


class TestPolicyBuilders(TestCase):
    """Tests for the default policy builder functions."""

    def test_ro_policy_structure(self):
        """The ro policy targets the base catalog with viewer, editor and admin roles."""
        policy = _build_ro_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - ro - marketing")
        self.assertEqual(policy.service, SERVICE_NAME)
        self.assertEqual(policy.zoneName, "marketing")
        self.assertEqual(policy.resources["catalog"].values, ["marketing"])
        self.assertEqual(len(policy.additionalResources), 3)
        item = policy.policyItems[0]
        self.assertEqual(
            sorted(item.roles),
            sorted(
                [
                    "marketing-viewer",
                    "marketing-editor",
                    "marketing-admin",
                ]
            ),
        )
        access_types = {a.type for a in item.accesses}
        self.assertEqual(access_types, {"select", "show", "use"})

    def test_rw_policy_structure(self):
        """The rw policy targets the developer catalog with editor+admin."""
        policy = _build_rw_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - rw - marketing")
        self.assertEqual(
            policy.resources["catalog"].values, ["marketing_developer"]
        )
        item = policy.policyItems[0]
        self.assertEqual(
            sorted(item.roles),
            ["marketing-admin", "marketing-editor"],
        )
        access_types = {a.type for a in item.accesses}
        self.assertEqual(
            access_types, {"select", "show", "use", "insert", "delete"}
        )

    def test_ddl_policy_structure(self):
        """The ddl policy targets the developer catalog schema/table with admin only."""
        policy = _build_ddl_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - ddl - marketing")
        self.assertEqual(
            policy.resources["catalog"].values, ["marketing_developer"]
        )
        self.assertIn("schema", policy.resources)
        self.assertEqual(len(policy.additionalResources), 1)
        item = policy.policyItems[0]
        self.assertEqual(item.roles, ["marketing-admin"])
        access_types = {a.type for a in item.accesses}
        self.assertEqual(access_types, {"alter", "create", "drop"})

    def test_is_policy_structure(self):
        """The is policy targets both catalogs with {USER} macro."""
        policy = _build_is_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - is - marketing")
        self.assertEqual(
            sorted(policy.resources["catalog"].values),
            ["marketing", "marketing_developer"],
        )
        self.assertEqual(len(policy.additionalResources), 3)
        item = policy.policyItems[0]
        self.assertEqual(item.users, ["{USER}"])
        self.assertIsNone(item.roles)
        access_types = {a.type for a in item.accesses}
        self.assertEqual(access_types, {"select", "show", "use"})
        second_block = policy.additionalResources[0]
        self.assertEqual(second_block["schema"].values, ["information_schema"])


class TestReconciler(TestCase):
    """Tests for the TrinoCatalogReconciler class."""

    def setUp(self):
        """Set up mock client and reconciler."""
        self.client = mock.MagicMock(
            spec_set=[
                "list_services_by_type",
                "list_zones",
                "list_roles",
                "list_policies",
                "get_zone",
                "get_role",
                "get_policy",
                "create_zone",
                "create_role",
                "create_policy",
                "update_policy",
                "delete_zone",
                "delete_role",
                "delete_policy_by_id",
            ]
        )
        self.client.list_zones.return_value = []
        self.client.list_roles.return_value = []
        self.client.list_policies.return_value = []
        self.reconciler = TrinoCatalogReconciler(self.client, SERVICE_NAME)

    def test_reconcile_creates_roles_zone_and_policies(self):
        """Reconciling a new catalog creates roles, zone, and 4 policies."""
        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.assertEqual(self.client.create_role.call_count, 4)
        created_roles = [
            call.args[0].name
            for call in self.client.create_role.call_args_list
        ]
        self.assertEqual(
            sorted(created_roles),
            [
                "marketing-admin",
                "marketing-auditor",
                "marketing-editor",
                "marketing-viewer",
            ],
        )

        self.assertEqual(self.client.create_zone.call_count, 1)
        zone = self.client.create_zone.call_args[0][0]
        self.assertEqual(zone.name, "marketing")
        self.assertIn(SERVICE_NAME, zone.services)

        self.assertEqual(self.client.create_policy.call_count, 4)
        created_policies = [
            call.args[0].name
            for call in self.client.create_policy.call_args_list
        ]
        self.assertEqual(
            sorted(created_policies),
            [
                "default - ddl - marketing",
                "default - is - marketing",
                "default - ro - marketing",
                "default - rw - marketing",
            ],
        )

    def test_reconcile_skips_existing_roles_and_zones(self):
        """Existing roles and zones are not recreated."""
        self.client.list_zones.return_value = [
            RangerSecurityZone({"name": "marketing"})
        ]
        self.client.list_roles.return_value = [
            RangerRole({"name": "marketing-viewer"}),
            RangerRole({"name": "marketing-editor"}),
            RangerRole({"name": "marketing-admin"}),
            RangerRole({"name": "marketing-auditor"}),
        ]

        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()

    def test_reconcile_developer_and_base_same_zone(self):
        """Both base and developer catalogs map to a single zone."""
        catalogs = [
            {"name": "marketing"},
            {"name": "marketing_developer"},
        ]
        self.reconciler.reconcile(catalogs)

        self.assertEqual(self.client.create_zone.call_count, 1)
        zone = self.client.create_zone.call_args[0][0]
        self.assertEqual(zone.name, "marketing")

    def test_reconcile_cleans_stale_zone_with_defaults_only(self):
        """A stale zone with only default policies is deleted."""
        self.client.list_zones.return_value = [
            RangerSecurityZone({"name": "old_zone"})
        ]
        self.client.list_roles.return_value = []

        default_policies = [
            RangerPolicy({"name": f"default - {s} - old_zone"})
            for s in ("ro", "rw", "ddl", "is")
        ]
        self.client.list_policies.return_value = default_policies

        self.reconciler.reconcile([])

        self.client.delete_zone.assert_called_once_with("old_zone")
        self.assertEqual(self.client.delete_role.call_count, 4)
        deleted_roles = [
            call.args[0] for call in self.client.delete_role.call_args_list
        ]
        self.assertEqual(
            sorted(deleted_roles),
            [
                "old_zone-admin",
                "old_zone-auditor",
                "old_zone-editor",
                "old_zone-viewer",
            ],
        )

    def test_reconcile_preserves_stale_zone_with_custom_policies(self):
        """A stale zone with custom policies is preserved."""
        self.client.list_zones.return_value = [
            RangerSecurityZone({"name": "old_zone"})
        ]
        self.client.list_roles.return_value = []

        policies = [
            RangerPolicy({"name": "default - ro - old_zone"}),
            RangerPolicy({"name": "my_custom_policy"}),
        ]
        self.client.list_policies.return_value = policies

        self.reconciler.reconcile([])

        self.client.delete_zone.assert_not_called()
        self.client.delete_role.assert_not_called()

    def test_reconcile_empty_catalogs_no_zones(self):
        """Empty catalogs with no existing zones is a no-op."""
        self.reconciler.reconcile([])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()

    def test_reconcile_updates_policy_when_changed(self):
        """An existing policy with different items is updated."""
        existing_policy = _build_ro_policy("marketing", SERVICE_NAME)
        existing_policy.id = 42
        existing_policy.policyItems[0].roles = ["marketing-viewer"]

        self.client.list_policies.return_value = [existing_policy]

        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.client.update_policy.assert_called_once()
        update_call = self.client.update_policy.call_args
        self.assertEqual(update_call.args[0], 42)
        updated_policy = update_call.args[1]
        self.assertEqual(updated_policy.id, 42)

    def test_reconcile_skips_policy_update_when_unchanged(self):
        """An existing policy matching desired state is not updated."""
        existing_policy = _build_ro_policy("marketing", SERVICE_NAME)
        existing_policy.id = 42

        self.client.list_policies.return_value = [existing_policy]

        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.client.update_policy.assert_not_called()
        self.client.create_policy.assert_called()
        created_names = [
            c.args[0].name for c in self.client.create_policy.call_args_list
        ]
        self.assertNotIn("default - ro - marketing", created_names)

    def test_reconcile_delete_role_error_logged(self):
        """Failure to delete a stale role is logged but does not raise."""
        self.client.list_zones.return_value = [
            RangerSecurityZone({"name": "stale"})
        ]
        self.client.list_roles.return_value = []
        self.client.list_policies.return_value = []
        self.client.delete_role.side_effect = RangerAPIError("role not found")

        with self.assertLogs(level="WARNING") as logs:
            self.reconciler.reconcile([])

        self.client.delete_zone.assert_called_once_with("stale")
        self.assertTrue(
            any("could not delete role" in msg for msg in logs.output)
        )

    def test_zone_services_constrained_to_catalogs(self):
        """Zone resources are constrained to the zone's catalog pair."""
        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        zone = self.client.create_zone.call_args[0][0]
        zone_svc = zone.services[SERVICE_NAME]
        resources = zone_svc.resources[0]
        catalog_values = resources["catalog"]
        self.assertEqual(
            sorted(catalog_values),
            ["marketing", "marketing_developer"],
        )

    def test_zone_admin_and_audit_roles(self):
        """Zone admin and audit roles use zone-specific roles."""
        catalogs = [{"name": "sales"}]
        self.reconciler.reconcile(catalogs)

        zone = self.client.create_zone.call_args[0][0]
        self.assertEqual(zone.adminUsers, [])
        self.assertEqual(zone.adminRoles, ["sales-admin"])
        self.assertEqual(zone.auditUsers, [])
        self.assertEqual(zone.auditRoles, ["sales-auditor"])

    def test_zone_tag_services_always_empty(self):
        """Zone tagServices is always empty to avoid postCreate failures."""
        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        zone = self.client.create_zone.call_args[0][0]
        self.assertEqual(zone.tagServices, [])

    def test_reconcile_purges_auto_policies_after_zone_creation(self):
        """Ranger auto-generated policies are deleted after zone creation."""
        auto_policies = [
            RangerPolicy({"id": 100, "name": "auto-policy-1"}),
            RangerPolicy({"id": 101, "name": "auto-policy-2"}),
        ]

        call_count = {"n": 0}
        original_return = []

        def list_policies_side_effect(zone_name, service_name):  # noqa DCO010
            call_count["n"] += 1
            if call_count["n"] == 1:
                return auto_policies
            return original_return

        self.client.list_policies.side_effect = list_policies_side_effect

        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.assertEqual(self.client.delete_policy_by_id.call_count, 2)
        deleted_ids = [
            call.args[0]
            for call in self.client.delete_policy_by_id.call_args_list
        ]
        self.assertEqual(sorted(deleted_ids), [100, 101])

    def test_reconcile_no_purge_for_existing_zone(self):
        """Auto-policy purge only happens for newly created zones."""
        self.client.list_zones.return_value = [
            RangerSecurityZone({"name": "marketing"})
        ]
        self.client.list_roles.return_value = [
            RangerRole({"name": f"marketing{s}"})
            for s in ("-viewer", "-editor", "-admin", "-auditor")
        ]

        catalogs = [{"name": "marketing"}]
        self.reconciler.reconcile(catalogs)

        self.client.delete_policy_by_id.assert_not_called()
