#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scenario tests for relation reconciliation."""

import dataclasses
import pathlib
from unittest import mock

import pytest
import requests
from apache_ranger.model.ranger_security_zone import RangerSecurityZone
from apache_ranger.model.ranger_service import RangerService
from ops import testing

from charm import ApiProbe
from tests.unit.helpers import (
    LDAP_CREDENTIALS_CONTENT,
    POLICY_RELATION_DATA,
    RANGER,
    FakeRangerClient,
    build_admin_state,
    build_usersync_state,
    carry_forward,
    mock_ranger_api,
    ranger_container,
    services,
    workload_path,
)


def _service(name, relation_id=None, service_id=1, service_type="trino"):
    """Build a Ranger service, optionally stamped for a policy relation."""
    configs = {}
    if relation_id is not None:
        configs["username"] = f"relation_id_{relation_id}"
    return RangerService(
        {"id": service_id, "name": name, "type": service_type, "configs": configs}
    )


def _policy_relation(data=POLICY_RELATION_DATA):
    """Build a ready policy relation."""
    return testing.Relation("policy", remote_app_name="requiring-charm", remote_app_data=data)


def _trino_relation():
    """Build a ready Trino catalog relation."""
    return testing.Relation(
        "trino-catalog",
        remote_app_name="trino-k8s",
        remote_app_data={
            "trino_url": "http://trino-k8s:8080",
            "trino_catalogs": '[{"name": "sales"}]',
            "trino_credentials_secret_id": "secret:trino",  # nosec B105
        },
    )


def _opensearch_relation(user_secret, tls_secret):
    """Build an OpenSearch relation backed by Juju secrets."""
    return testing.Relation(
        "opensearch",
        remote_app_name="opensearch-k8s",
        remote_app_data={
            "endpoints": "opensearch:9200",
            "secret-user": user_secret.id,
            "secret-tls": tls_secret.id,
        },
    )


def test_services_created_for_live_policy_relations(ctx):
    """A ready policy relation creates a stamped Ranger service."""
    relation = _policy_relation()
    with mock_ranger_api() as client:
        ctx.run(ctx.on.relation_changed(relation), build_admin_state(extra_relations={relation}))

    created = [args[1] for args in client.calls if args[0] == "create_service"]
    assert len(created) == 1
    assert created[0].configs == {
        "username": f"relation_id_{relation.id}",
        "resource.lookup.timeout.value.in.ms": 3000,
        "jdbc.driverClassName": "io.trino.jdbc.TrinoDriver",
        "jdbc.url": "jdbc:trino://trino-k8s:8080",
    }


def test_service_not_created_twice(ctx):
    """A stamped service is retained by a subsequent reconciliation."""
    relation = _policy_relation()
    client = FakeRangerClient()
    with mock.patch("charm.RangerAPIClient", return_value=client):
        first = ctx.run(
            ctx.on.relation_changed(relation), build_admin_state(extra_relations={relation})
        )
        client.calls.clear()
        ctx.run(ctx.on.update_status(), carry_forward(first))

    assert not [call for call in client.calls if call[0] == "create_service"]


def test_service_not_created_when_name_taken(ctx, caplog):
    """An unmanaged service with the requested name is not adopted."""
    relation = _policy_relation()
    with mock_ranger_api(services=[_service("trino-service")]) as client:
        ctx.run(ctx.on.relation_changed(relation), build_admin_state(extra_relations={relation}))

    assert not [call for call in client.calls if call[0] == "create_service"]
    assert "already exists and is not managed" in caplog.text


def test_service_skipped_when_requirer_has_not_published(ctx):
    """An empty policy databag makes no Ranger write."""
    relation = _policy_relation({})
    with mock_ranger_api() as client:
        ctx.run(ctx.on.relation_changed(relation), build_admin_state(extra_relations={relation}))

    assert not [
        call for call in client.calls if call[0] in {"create_service", "delete_service_by_id"}
    ]


def test_services_garbage_collected_for_dead_relations(ctx):
    """A stamped service without an active relation is deleted."""
    with mock_ranger_api(services=[_service("orphan", relation_id=42)]) as client:
        ctx.run(ctx.on.update_status(), build_admin_state())

    assert [call[:2] for call in client.calls if call[0] == "delete_service_by_id"] == [
        ("delete_service_by_id", 1)
    ]


@pytest.mark.parametrize(
    "policies",
    [
        [{"name": "custom", "service": "orphan", "policyItems": []}],
        [
            {
                "name": "all - catalog",
                "service": "orphan",
                "policyItems": [{"users": []}],
            }
        ],
    ],
)
def test_service_not_deleted_when_custom_policies_exist(ctx, policies):
    """Services with user-managed or unstamped policies are retained."""
    from apache_ranger.model.ranger_policy import RangerPolicy

    ranger_policies = [RangerPolicy(policy) for policy in policies]
    with mock_ranger_api(
        services=[_service("orphan", relation_id=42)], policies=ranger_policies
    ) as client:
        ctx.run(ctx.on.update_status(), build_admin_state())

    assert not [call for call in client.calls if call[0] == "delete_service_by_id"]


def test_policy_relation_broken_converges_in_hook(ctx):
    """Breaking a policy relation deletes its unstamped-policy-free service immediately."""
    relation = _policy_relation()
    with mock_ranger_api(services=[_service("trino-service", relation.id)]) as client:
        ctx.run(
            ctx.on.relation_broken(relation),
            build_admin_state(extra_relations={relation}),
        )

    assert [call[:2] for call in client.calls if call[0] == "delete_service_by_id"] == [
        ("delete_service_by_id", 1)
    ]


def test_unstamped_services_are_never_touched(ctx):
    """A service without a relation stamp is neither adopted nor deleted."""
    with mock_ranger_api(services=[_service("external")]) as client:
        ctx.run(ctx.on.update_status(), build_admin_state())

    assert not [call for call in client.calls if call[0] == "delete_service_by_id"]


def test_service_reconciliation_survives_partial_failure(ctx):
    """A failed service creation does not prevent another relation from converging."""
    first = _policy_relation({**POLICY_RELATION_DATA, "name": "first"})
    second = _policy_relation({**POLICY_RELATION_DATA, "name": "second"})
    with mock_ranger_api(failure={"create_service": 1}) as client:
        ctx.run(
            ctx.on.config_changed(),
            build_admin_state(extra_relations={first, second}),
        )

    assert len(client.services) == 1


def test_api_phase_skipped_when_unreachable(ctx):
    """An unavailable Ranger API leaves the local workload configured and not blocked."""
    relation = _policy_relation()
    with mock_ranger_api(probe=ApiProbe.UNREACHABLE) as client:
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            build_admin_state(extra_relations={relation}),
        )

    assert RANGER in services(state_out)
    assert not [call for call in client.calls if call[0] == "create_service"]
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_api_credential_rejection_blocks(ctx):
    """Rejected administrator credentials block without resource mutations."""
    with mock_ranger_api(probe=ApiProbe.REJECTED) as client:
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state())

    assert state_out.unit_status == testing.BlockedStatus(
        "Ranger authentication failed for admin. Revert the system-users secret or change "
        "the password in the Ranger UI."
    )
    assert len(client.calls) == 2


def test_api_phase_skipped_on_non_leader(ctx):
    """A follower probes credentials but does not reconcile shared Ranger resources."""
    truststore = testing.Secret({"password": "truststore"}, label="truststore-password")  # nosec B105
    with mock_ranger_api() as client:
        ctx.run(
            ctx.on.config_changed(),
            build_admin_state(leader=False, extra_secrets=(truststore,)),
        )

    assert [call[0] for call in client.calls] == ["authenticate", "authenticate"]


def test_api_phase_skipped_for_usersync(ctx):
    """Usersync probes its configured manager but does not reconcile Ranger resources."""
    with mock_ranger_api() as client:
        ctx.run(ctx.on.config_changed(), build_usersync_state())

    assert [call[0] for call in client.calls] == ["authenticate", "authenticate"]


def test_policy_manager_url_published_while_api_down(ctx):
    """Policy consumers receive the manager URL even while the API is unavailable."""
    policy = _policy_relation()
    with mock_ranger_api(probe=ApiProbe.UNREACHABLE):
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state(extra_relations={policy}))

    policy_out = next(relation for relation in state_out.relations if relation.id == policy.id)
    assert policy_out.local_app_data == {
        "policy_manager_url": "http://ranger-k8s.ranger-model.svc.cluster.local:6080"
    }


def test_api_call_budget(ctx):
    """A steady-state admin update uses no more than ten Ranger API calls."""
    trino = _trino_relation()
    zone = RangerSecurityZone({"name": "sales"})
    client = FakeRangerClient(services=[_service("trino-service")], zones=[zone])
    with mock.patch("charm.RangerAPIClient", return_value=client):
        ctx.run(ctx.on.update_status(), build_admin_state(extra_relations={trino}))

    assert len(client.calls) <= 10
    assert len(client.calls) == 8


def test_trino_catalogs_derived_from_relation(ctx):
    """Trino catalog reconciliation receives catalogs from relation data."""
    trino = _trino_relation()
    reconciler = mock.MagicMock()
    with mock.patch("relations.trino.TrinoCatalogReconciler", return_value=reconciler):
        with mock_ranger_api(services=[_service("trino-service")]):
            ctx.run(ctx.on.relation_changed(trino), build_admin_state(extra_relations={trino}))

    assert reconciler.reconcile.call_args.args[0][0]["name"] == "sales"


def test_trino_reconciliation_threads_strict_config(ctx):
    """The strict reconciliation configuration reaches the catalog reconciler."""
    trino = _trino_relation()
    reconciler = mock.MagicMock()
    with mock.patch("relations.trino.TrinoCatalogReconciler", return_value=reconciler):
        with mock_ranger_api(services=[_service("trino-service")]):
            ctx.run(
                ctx.on.config_changed(),
                build_admin_state(
                    config={"enforce-strict-reconciliation": False},
                    extra_relations={trino},
                ),
            )

    assert reconciler.reconcile.call_args.kwargs["strict"] is False


@pytest.mark.parametrize(
    ("leader", "expected"),
    [
        (True, testing.BlockedStatus("Trino service not found in Ranger")),
        (False, testing.WaitingStatus("waiting for leader to create the truststore secret")),
    ],
)
def test_trino_service_missing_blocks_leader_only(ctx, leader, expected):
    """Only the leader reports a missing Ranger Trino service."""
    trino = _trino_relation()
    state = build_admin_state(leader=leader, extra_relations={trino})
    if not leader:
        truststore = testing.Secret({"password": "truststore"}, label="truststore-password")  # nosec B105
        state = dataclasses.replace(state, secrets={*state.secrets, truststore})
        expected = testing.MaintenanceStatus("waiting for workload")
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.collect_unit_status(), state)

    assert state_out.unit_status == expected


def test_trino_service_missing_does_not_block_when_api_unreachable(ctx):
    """An unavailable API lets status evaluation continue to workload health."""
    trino = _trino_relation()
    with mock_ranger_api(probe=ApiProbe.UNREACHABLE):
        state_out = ctx.run(
            ctx.on.collect_unit_status(), build_admin_state(extra_relations={trino})
        )

    assert state_out.unit_status == testing.MaintenanceStatus("waiting for workload")


def test_trino_relation_broken_does_not_error(ctx):
    """Breaking an unconfigured Trino relation completes cleanly."""
    trino = testing.Relation("trino-catalog", remote_app_name="trino-k8s")
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.relation_broken(trino), build_admin_state(extra_relations={trino})
        )

    assert state_out.deferred == []


def test_ldap_bind_user_published(ctx):
    """A usersync leader publishes its LDAP bind user during reconciliation."""
    ldap = testing.Relation("ldap", remote_app_name="openldap")
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(
                database=None,
                config={
                    "charm-function": "usersync",
                    "policy-mgr-url": "http://ranger-k8s:6080",
                },
                extra_relations={
                    dataclasses.replace(
                        ldap,
                        remote_app_data={
                            "admin_password": "password",  # nosec B105
                            "base_dn": "dc=canonical,dc=com",
                            "ldap_url": "ldap://openldap:389",
                        },
                    )
                },
            ),
        )

    ldap_out = next(relation for relation in state_out.relations if relation.id == ldap.id)
    assert ldap_out.local_app_data == {"user": "admin"}


def test_ldap_relation_values_override_secret_and_config_per_key(ctx):
    """LDAP relation values take precedence over configured fallback values."""
    secret = testing.Secret(LDAP_CREDENTIALS_CONTENT)
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.config_changed(),
            build_usersync_state(
                config={
                    "ldap-credentials": secret.id,
                    "sync-ldap-url": "ldap://configured:389",
                },
                extra_secrets=(secret,),
            ),
        )

    environment = services(state_out)[RANGER]["environment"]
    assert environment["SYNC_LDAP_URL"] == "ldap://comsys-openldap-k8s:389"
    assert environment["SYNC_LDAP_BIND_DN"] == "cn=admin,dc=canonical,dc=dev,dc=com"
    assert environment["SYNC_LDAP_BIND_PASSWORD"] == "huedw7uiedw7"


def test_usersync_blocks_without_ldap_source(ctx):
    """Usersync requires either LDAP relation data or complete fallback settings."""
    state_out = ctx.run(
        ctx.on.config_changed(),
        build_admin_state(
            database=None,
            config={
                "charm-function": "usersync",
                "policy-mgr-url": "http://ranger-k8s:6080",
            },
        ),
    )

    assert state_out.unit_status == testing.BlockedStatus(
        "Missing required LDAP configuration: ldap-credentials, sync-ldap-url, "
        "sync-ldap-search-base."
    )


def test_opensearch_connection_derived_from_relation(ctx):
    """OpenSearch relation data and secrets render the admin environment."""
    user = testing.Secret({"username": "ranger", "password": "password"})  # nosec B105
    tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\nfirst\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\nsecond\n-----END CERTIFICATE-----"
            )
        }
    )
    relation = _opensearch_relation(user, tls)
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            build_admin_state(extra_relations={relation}, extra_secrets=(user, tls)),
        )

    environment = services(state_out)[RANGER]["environment"]
    assert {
        key: environment[key]
        for key in ("OPENSEARCH_ENABLED", "OPENSEARCH_HOST", "OPENSEARCH_PORT", "OPENSEARCH_USER")
    } == {
        "OPENSEARCH_ENABLED": True,
        "OPENSEARCH_HOST": "opensearch",
        "OPENSEARCH_PORT": "9200",
        "OPENSEARCH_USER": "ranger",
    }


def test_opensearch_disabled_when_relation_absent(ctx):
    """Without OpenSearch, reconciliation does not invoke keytool import."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state())

    assert not workload_path(state_out, ctx, "/opensearch.crt").exists()


def test_opensearch_certificate_imported_on_non_leader(ctx):
    """A follower imports the related OpenSearch CA into its local truststore."""
    user = testing.Secret({"username": "ranger", "password": "password"})  # nosec B105
    tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\nfirst\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\nsecond\n-----END CERTIFICATE-----"
            )
        }
    )
    truststore = testing.Secret({"password": "truststore"}, label="truststore-password")  # nosec B105
    relation = _opensearch_relation(user, tls)
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.relation_changed(relation),
            build_admin_state(
                leader=False,
                extra_relations={relation},
                extra_secrets=(user, tls, truststore),
            ),
        )

    assert workload_path(state_out, ctx, "/opensearch.crt").exists()


def test_opensearch_cert_hash_changes_layer(ctx):
    """Rotating the OpenSearch CA changes the rendered Pebble environment."""
    user = testing.Secret({"username": "ranger", "password": "password"})  # nosec B105
    first_tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\none\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\ntwo\n-----END CERTIFICATE-----"
            )
        }
    )
    second_tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\none\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\nthree\n-----END CERTIFICATE-----"
            )
        }
    )
    with mock_ranger_api():
        first = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(
                extra_relations={_opensearch_relation(user, first_tls)},
                extra_secrets=(user, first_tls),
            ),
        )
        second = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(
                extra_relations={_opensearch_relation(user, second_tls)},
                extra_secrets=(user, second_tls),
            ),
        )

    assert (
        services(first)[RANGER]["environment"]["OPENSEARCH_CERT_HASH"]
        != services(second)[RANGER]["environment"]["OPENSEARCH_CERT_HASH"]
    )


def test_opensearch_unchanged_certificate_preserves_layer(ctx):
    """Unchanged OpenSearch CA input produces an identical Pebble plan."""
    user = testing.Secret({"username": "ranger", "password": "password"})  # nosec B105
    tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\none\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\ntwo\n-----END CERTIFICATE-----"
            )
        }
    )
    truststore = testing.Secret({"password": "truststore"}, label="truststore-password")  # nosec B105
    relation = _opensearch_relation(user, tls)
    with mock.patch("relations.opensearch.requests.put"):
        with mock_ranger_api():
            first = ctx.run(
                ctx.on.config_changed(),
                build_admin_state(
                    extra_relations={relation},
                    extra_secrets=(user, tls, truststore),
                ),
            )
            second = ctx.run(ctx.on.update_status(), carry_forward(first))

    assert second.get_container(RANGER).plan == first.get_container(RANGER).plan


def test_opensearch_certificate_removed_when_relation_broken(ctx):
    """Breaking OpenSearch removes its certificate from the truststore."""
    relation = testing.Relation(
        "opensearch",
        remote_app_name="opensearch-k8s",
        remote_app_data={"endpoints": "opensearch:9200"},
    )
    container = ranger_container(
        mounts={
            "/opensearch.crt": testing.Mount(
                location="/opensearch.crt",
                source=pathlib.Path(__file__).parent / "__init__.py",
            )
        }
    )
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.relation_broken(relation),
            build_admin_state(extra_relations={relation}, container=container),
        )

    assert not workload_path(state_out, ctx, "/opensearch.crt").exists()
    assert services(state_out)[RANGER]["environment"]["OPENSEARCH_CERT_HASH"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_opensearch_index_mapping_failure_does_not_error_hook(ctx):
    """A failed OpenSearch mapping request leaves the workload rendered."""
    user = testing.Secret({"username": "ranger", "password": "password"})  # nosec B105
    tls = testing.Secret(
        {
            "tls-ca": (
                "-----BEGIN CERTIFICATE-----\nfirst\n-----END CERTIFICATE-----\n"
                "-----BEGIN CERTIFICATE-----\nsecond\n-----END CERTIFICATE-----"
            )
        }
    )
    relation = _opensearch_relation(user, tls)
    with mock.patch(
        "relations.opensearch.requests.put", side_effect=requests.exceptions.RequestException
    ):
        with mock_ranger_api():
            state_out = ctx.run(
                ctx.on.relation_changed(relation),
                build_admin_state(extra_relations={relation}, extra_secrets=(user, tls)),
            )

    assert RANGER in services(state_out)


def test_opensearch_blocked_for_usersync(ctx):
    """Usersync blocks when related to OpenSearch."""
    relation = testing.Relation("opensearch", remote_app_name="opensearch-k8s")
    state_out = ctx.run(
        ctx.on.relation_changed(relation),
        build_usersync_state(extra_relations={relation}),
    )

    assert state_out.unit_status == testing.BlockedStatus(
        "Only Ranger admin can relate to OpenSearch."
    )
