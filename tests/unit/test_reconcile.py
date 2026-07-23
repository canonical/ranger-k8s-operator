# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the Trino catalog reconciler."""

# pylint:disable=protected-access

from unittest import TestCase, mock

from apache_ranger.model.ranger_policy import RangerPolicy
from apache_ranger.model.ranger_role import RangerRole
from apache_ranger.model.ranger_security_zone import RangerSecurityZone

from literals import DEFAULT_POLICIES
from ranger_client import RangerAPIError
from reconcile import (
    TrinoCatalogReconciler,
    _build_ddl_policy,
    _build_is_policy,
    _build_ro_policy,
    _build_rw_policy,
    _catalogs_to_zones,
    _role_names,
)

SERVICE_NAME = "trino-service"


class TestCatalogsToZones(TestCase):
    """Tests for the catalog-to-zone mapping function."""

    def test_base_catalog_only(self):
        """A single base catalog maps to one zone."""
        self.assertEqual(_catalogs_to_zones([{"name": "marketing"}]), {"marketing"})

    def test_developer_catalog_only(self):
        """A developer catalog maps to the base zone name."""
        self.assertEqual(_catalogs_to_zones([{"name": "marketing_developer"}]), {"marketing"})

    def test_base_and_developer(self):
        """Both base and developer catalogs map to one zone."""
        catalogs = [{"name": "marketing"}, {"name": "marketing_developer"}]
        self.assertEqual(_catalogs_to_zones(catalogs), {"marketing"})

    def test_multiple_zones(self):
        """Multiple catalog pairs produce multiple zones."""
        catalogs = [
            {"name": "marketing"},
            {"name": "marketing_developer"},
            {"name": "sales"},
            {"name": "finance_developer"},
        ]
        self.assertEqual(_catalogs_to_zones(catalogs), {"marketing", "sales", "finance"})

    def test_empty_catalogs(self):
        """No catalogs produce no zones."""
        self.assertEqual(_catalogs_to_zones([]), set())


class TestHelpers(TestCase):
    """Tests for reconciliation naming helpers."""

    def test_role_names(self):
        """Role names follow the expected pattern."""
        self.assertEqual(
            _role_names("marketing"),
            [
                "marketing-viewer",
                "marketing-editor",
                "marketing-admin",
                "marketing-auditor",
            ],
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
        self.assertEqual(
            sorted(policy.policyItems[0].roles),
            ["marketing-admin", "marketing-editor", "marketing-viewer"],
        )
        self.assertEqual(
            {access.type for access in policy.policyItems[0].accesses}, {"select", "show", "use"}
        )

    def test_rw_policy_structure(self):
        """The rw policy targets the developer catalog with editor and admin roles."""
        policy = _build_rw_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - rw - marketing")
        self.assertEqual(policy.resources["catalog"].values, ["marketing_developer"])
        self.assertEqual(
            sorted(policy.policyItems[0].roles), ["marketing-admin", "marketing-editor"]
        )
        self.assertEqual(
            {access.type for access in policy.policyItems[0].accesses},
            {"select", "show", "use", "insert", "delete"},
        )

    def test_ddl_policy_structure(self):
        """The ddl policy targets the developer catalog schema and table with admin only."""
        policy = _build_ddl_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - ddl - marketing")
        self.assertEqual(policy.resources["catalog"].values, ["marketing_developer"])
        self.assertIn("schema", policy.resources)
        self.assertEqual(len(policy.additionalResources), 1)
        self.assertEqual(policy.policyItems[0].roles, ["marketing-admin"])
        self.assertEqual(
            {access.type for access in policy.policyItems[0].accesses},
            {"alter", "create", "drop"},
        )

    def test_is_policy_structure(self):
        """The is policy targets both catalogs with the user macro."""
        policy = _build_is_policy("marketing", SERVICE_NAME)
        self.assertEqual(policy.name, "default - is - marketing")
        self.assertEqual(
            sorted(policy.resources["catalog"].values),
            ["marketing", "marketing_developer"],
        )
        self.assertEqual(len(policy.additionalResources), 3)
        self.assertEqual(policy.policyItems[0].users, ["{USER}"])
        self.assertIsNone(policy.policyItems[0].roles)
        self.assertEqual(
            {access.type for access in policy.policyItems[0].accesses}, {"select", "show", "use"}
        )
        self.assertEqual(
            policy.additionalResources[0]["schema"].values,
            ["information_schema"],
        )


class TestReconciler(TestCase):
    """Tests for the TrinoCatalogReconciler class."""

    def setUp(self):
        """Set up a reconciler with an empty Ranger snapshot."""
        self.client = mock.MagicMock(
            spec_set=[
                "list_zones",
                "list_roles",
                "list_service_policies",
                "list_policies",
                "create_zone",
                "create_role",
                "create_policy",
                "delete_policy_by_id",
            ]
        )
        self.client.list_zones.return_value = []
        self.client.list_roles.return_value = []
        self.client.list_service_policies.return_value = []
        self.client.list_policies.return_value = []
        self.reconciler = TrinoCatalogReconciler(self.client, SERVICE_NAME)

    def _existing_zone(self, name="marketing"):
        """Make the named zone present in the shared snapshot."""
        self.client.list_zones.return_value = [RangerSecurityZone({"name": name})]

    def _existing_roles(self, name="marketing", **members):
        """Make all management roles present, optionally with memberships."""
        self.client.list_roles.return_value = [
            RangerRole({"name": role_name, **members}) for role_name in _role_names(name)
        ]

    def test_reconcile_creates_roles_zone_and_policies_from_shared_snapshot(self):
        """A new catalog creates its resources and purges Ranger auto-policies."""
        auto_policies = [
            RangerPolicy({"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}),
            RangerPolicy({"id": 101, "name": DEFAULT_POLICIES[1], "zoneName": "marketing"}),
        ]
        self.client.list_policies.return_value = auto_policies

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.list_zones.call_count, 1)
        self.assertEqual(self.client.list_roles.call_count, 1)
        self.client.list_service_policies.assert_called_once_with(SERVICE_NAME)
        self.client.list_policies.assert_called_once_with("marketing", SERVICE_NAME)
        self.assertEqual(self.client.create_role.call_count, 4)
        self.assertEqual(self.client.create_zone.call_count, 1)
        self.assertEqual(self.client.create_policy.call_count, 4)
        self.client.delete_policy_by_id.assert_has_calls([mock.call(100), mock.call(101)])

    def test_reconcile_first_create_purges_auto_policies_from_targeted_refetch(self):
        """A first create purges auto-policies returned only by the targeted re-fetch."""
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_policies.return_value = [auto_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.create_role.call_count, 4)
        self.client.create_zone.assert_called_once()
        self.assertEqual(self.client.create_policy.call_count, 4)
        self.client.delete_policy_by_id.assert_called_once_with(100)

    def test_reconcile_allows_absent_roles_in_strict_mode(self):
        """Absent corresponding roles permit zone creation."""
        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_zone.assert_called_once()

    def test_reconcile_allows_empty_roles_in_strict_mode(self):
        """Empty corresponding roles permit zone creation."""
        self._existing_roles()

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_called_once()

    def test_reconcile_skips_populated_roles_in_strict_mode(self):
        """Any corresponding role membership leaves a new catalog unprovisioned."""
        self.client.list_roles.return_value = [
            RangerRole({"name": "marketing-viewer", "users": [{"name": "alice"}]})
        ]

        with self.assertLogs("reconcile", "WARNING") as logs:
            self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()
        self.assertIn("left unprovisioned", "\n".join(logs.output))

    def test_reconcile_fail_open_creates_onto_populated_roles(self):
        """Disabling strict mode creates a zone despite populated roles."""
        self._existing_roles(groups=[{"name": "analysts"}])

        with self.assertLogs("reconcile", "WARNING") as logs:
            self.reconciler.reconcile([{"name": "marketing"}], strict=False)

        self.client.create_zone.assert_called_once()
        self.assertIn("fail-open", "\n".join(logs.output))

    def test_reconcile_freezes_when_creation_is_disabled(self):
        """Creation-disabled reconciliation makes no changes."""
        self.reconciler.reconcile([{"name": "marketing"}], create_enabled=False)

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()
        self.client.delete_policy_by_id.assert_not_called()
        self.client.list_policies.assert_not_called()

    def test_reconcile_never_modifies_completed_zone(self):
        """A completed zone's roles and policies remain entirely untouched."""
        self._existing_zone()
        self._existing_roles()
        edited_policy = _build_ro_policy("marketing", SERVICE_NAME)
        edited_policy.policyItems[0].roles = ["marketing-viewer"]
        self.client.list_service_policies.return_value = [edited_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()
        self.client.delete_policy_by_id.assert_not_called()

    def test_reconcile_resumes_zone_with_auto_policies(self):
        """A zone with Ranger's auto-policies resumes provisioning and purges them."""
        self._existing_zone()
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_service_policies.return_value = [auto_policy]
        current_auto_policy = RangerPolicy(
            {"id": 101, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_policies.return_value = [current_auto_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.create_role.call_count, 4)
        self.client.create_zone.assert_not_called()
        self.assertEqual(self.client.create_policy.call_count, 4)
        self.client.list_policies.assert_called_once_with("marketing", SERVICE_NAME)
        self.client.delete_policy_by_id.assert_called_once_with(101)

    def test_reconcile_skips_zone_without_auto_policies(self):
        """A zone without auto-policies is considered complete."""
        self._existing_zone()
        self._existing_roles()

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()
        self.client.delete_policy_by_id.assert_not_called()
        self.client.list_policies.assert_not_called()

    def test_reconcile_creates_roles_before_zone_without_rollback(self):
        """Role creation survives a failed zone create so the next run can resume."""
        self.client.list_roles.return_value = [RangerRole({"name": "marketing-viewer"})]
        self.client.create_zone.side_effect = RangerAPIError("zone unavailable")

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.create_role.call_count, 3)
        self.client.create_zone.assert_called_once()
        self.client.create_policy.assert_not_called()
        self.client.delete_policy_by_id.assert_not_called()
        call_names = [call[0] for call in self.client.mock_calls]
        self.assertLess(call_names.index("create_role"), call_names.index("create_zone"))
