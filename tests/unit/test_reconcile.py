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
from reconcile import TrinoCatalogReconciler, _catalogs_to_zones, _role_names

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


class TestReconciler(TestCase):
    """Tests for the TrinoCatalogReconciler class."""

    def setUp(self):
        """Set up mock client and reconciler."""
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

    def _resume_zone(self):
        """Configure the shared snapshot to represent an incomplete existing zone."""
        self._existing_zone()
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        current_auto_policy = RangerPolicy(
            {"id": 101, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_service_policies.return_value = [auto_policy]
        self.client.list_policies.return_value = [current_auto_policy]
        self.assertIn(auto_policy.name, DEFAULT_POLICIES)
        return current_auto_policy

    def _created_policies(self):
        """Return policies submitted to Ranger keyed by their managed names."""
        policies = {
            call.args[0].name: call.args[0] for call in self.client.create_policy.call_args_list
        }
        self.assertEqual(
            set(policies),
            {f"default - {suffix} - marketing" for suffix in ("ro", "rw", "ddl", "is")},
        )
        return policies

    def _assert_policy_roles(self, policies, suffix, expected_roles):
        """Assert the role principals for a created managed policy."""
        self.assertCountEqual(
            policies[f"default - {suffix} - marketing"].policyItems[0].roles,
            expected_roles,
        )

    def _assert_is_policy(self, policies):
        """Assert the information-schema policy still grants through the user macro."""
        policy_items = policies["default - is - marketing"].policyItems
        self.assertEqual(len(policy_items), 1)
        self.assertEqual(policy_items[0].users, ["{USER}"])
        self.assertIsNone(policy_items[0].roles)

    def _assert_auto_policy_purged(self, auto_policy):
        """Assert an incomplete zone is finalized by removing its auto-policy."""
        self.client.delete_policy_by_id.assert_called_once_with(auto_policy.id)

    def test_create_path_filters_a_populated_admin_from_ro_policy(self):
        """Strict creation retains empty ro roles while omitting populated admin."""
        self._existing_roles()
        self.client.list_roles.return_value[-2].users = [{"name": "alice"}]
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_policies.return_value = [auto_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_zone.assert_called_once()
        policies = self._created_policies()
        self._assert_policy_roles(
            policies,
            "ro",
            ["marketing-viewer", "marketing-editor"],
        )
        self._assert_auto_policy_purged(auto_policy)

    def test_resume_path_filters_a_populated_admin_from_ro_policy(self):
        """Strict resume retains empty ro roles while omitting populated admin."""
        self._existing_roles()
        self.client.list_roles.return_value[-2].users = [{"name": "alice"}]
        auto_policy = self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_zone.assert_not_called()
        policies = self._created_policies()
        self._assert_policy_roles(
            policies,
            "ro",
            ["marketing-viewer", "marketing-editor"],
        )
        self._assert_auto_policy_purged(auto_policy)

    def test_create_path_creates_audit_only_shells_for_populated_roles(self):
        """Strict creation emits empty policy items when every role is populated."""
        self._existing_roles(users=[{"name": "alice"}])

        self.reconciler.reconcile([{"name": "marketing"}])

        policies = self._created_policies()
        for suffix in ("ro", "rw", "ddl"):
            self.assertEqual(policies[f"default - {suffix} - marketing"].policyItems, [])

    def test_resume_path_creates_audit_only_shells_for_populated_roles(self):
        """Strict resume emits empty policy items when every role is populated."""
        self._existing_roles(users=[{"name": "alice"}])
        self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}])

        policies = self._created_policies()
        for suffix in ("ro", "rw", "ddl"):
            self.assertEqual(policies[f"default - {suffix} - marketing"].policyItems, [])

    def test_create_path_keeps_is_policy_for_populated_roles(self):
        """Strict creation keeps the is user-macro item when all roles are populated."""
        self._existing_roles(users=[{"name": "alice"}])

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_is_policy(self._created_policies())

    def test_resume_path_keeps_is_policy_for_populated_roles(self):
        """Strict resume keeps the is user-macro item when all roles are populated."""
        self._existing_roles(users=[{"name": "alice"}])
        self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_is_policy(self._created_policies())

    def test_create_path_non_strict_keeps_all_role_principals(self):
        """Non-strict creation does not filter populated roles from any policy."""
        self._existing_roles(groups=[{"name": "analysts"}])

        self.reconciler.reconcile([{"name": "marketing"}], strict=False)

        policies = self._created_policies()
        self._assert_policy_roles(
            policies,
            "ro",
            ["marketing-viewer", "marketing-editor", "marketing-admin"],
        )
        self._assert_policy_roles(policies, "rw", ["marketing-editor", "marketing-admin"])
        self._assert_policy_roles(policies, "ddl", ["marketing-admin"])
        self._assert_is_policy(policies)

    def test_resume_path_non_strict_keeps_all_role_principals(self):
        """Non-strict resume does not filter populated roles from any policy."""
        self._existing_roles(groups=[{"name": "analysts"}])
        self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}], strict=False)

        policies = self._created_policies()
        self._assert_policy_roles(
            policies,
            "ro",
            ["marketing-viewer", "marketing-editor", "marketing-admin"],
        )
        self._assert_policy_roles(policies, "rw", ["marketing-editor", "marketing-admin"])
        self._assert_policy_roles(policies, "ddl", ["marketing-admin"])
        self._assert_is_policy(policies)

    def test_create_path_treats_empty_nested_roles_block_as_populated(self):
        """Strict creation conservatively filters a role with an empty nested-role block."""
        self._existing_roles()
        self.client.list_roles.return_value[-2].roles = []

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_policy_roles(
            self._created_policies(),
            "ro",
            ["marketing-viewer", "marketing-editor"],
        )

    def test_resume_path_treats_empty_nested_roles_block_as_populated(self):
        """Strict resume conservatively filters a role with an empty nested-role block."""
        self._existing_roles()
        self.client.list_roles.return_value[-2].roles = []
        self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_policy_roles(
            self._created_policies(),
            "ro",
            ["marketing-viewer", "marketing-editor"],
        )

    def test_create_path_purges_auto_policies_after_every_policy_is_shelled(self):
        """Creation finalizes an all-shell zone by purging its auto-policy."""
        self._existing_roles(users=[{"name": "alice"}])
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_policies.return_value = [auto_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_auto_policy_purged(auto_policy)

    def test_resume_path_purges_auto_policies_after_every_policy_is_shelled(self):
        """Resume finalizes an all-shell zone by purging its auto-policy."""
        self._existing_roles(users=[{"name": "alice"}])
        auto_policy = self._resume_zone()

        self.reconciler.reconcile([{"name": "marketing"}])

        self._assert_auto_policy_purged(auto_policy)

    def test_create_path_purges_auto_policies_after_policy_creation_error(self):
        """Creation finalizes the zone when Ranger rejects a managed policy."""
        auto_policy = RangerPolicy(
            {"id": 100, "name": DEFAULT_POLICIES[0], "zoneName": "marketing"}
        )
        self.client.list_policies.return_value = [auto_policy]
        self.client.create_policy.side_effect = RangerAPIError("policy unavailable")

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.create_policy.call_count, 4)
        self._assert_auto_policy_purged(auto_policy)

    def test_resume_path_purges_auto_policies_after_policy_creation_error(self):
        """Resume finalizes the zone when Ranger rejects a managed policy."""
        auto_policy = self._resume_zone()
        self.client.create_policy.side_effect = RangerAPIError("policy unavailable")

        self.reconciler.reconcile([{"name": "marketing"}])

        self.assertEqual(self.client.create_policy.call_count, 4)
        self._assert_auto_policy_purged(auto_policy)

    def test_reconcile_logs_one_strict_opt_out_message_for_an_empty_run(self):
        """Disabling strict reconciliation logs the authorized opt-out once per run."""
        with self.assertLogs("reconcile", level="INFO") as logs:
            self.reconciler.reconcile([], strict=False)

        self.assertEqual(
            logs.output,
            [
                "INFO:reconcile:strict reconciliation is disabled; "
                "this run is an authorized security opt-out"
            ],
        )

    def test_reconcile_never_modifies_completed_zone(self):
        """A completed zone's roles and policies remain entirely untouched."""
        self._existing_zone()
        self._existing_roles()
        edited_policy = RangerPolicy({"name": "default - ro - marketing", "zoneName": "marketing"})
        self.client.list_service_policies.return_value = [edited_policy]

        self.reconciler.reconcile([{"name": "marketing"}])

        self.client.create_role.assert_not_called()
        self.client.create_zone.assert_not_called()
        self.client.create_policy.assert_not_called()
        self.client.delete_policy_by_id.assert_not_called()

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
