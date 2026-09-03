#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-level reconciliation tests."""

import dataclasses

import pytest
from ops import testing
from ops._private.harness import ActionFailed
from ops.pebble import CheckStatus

from tests.unit.helpers import (
    DATABASE_CONNECTION,
    LDAP_CREDENTIALS_CONTENT,
    RANGER,
    SYSTEM_USERS_SECRET_CONTENT,
    build_admin_state,
    build_usersync_state,
    carry_forward,
    mock_ranger_api,
    ranger_container,
    services,
    workload_path,
)


def test_admin_ready(ctx):
    """A ready admin renders its workload service, check, and exposed port."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state())

    service = services(state_out)[RANGER]
    assert service["command"] == "/home/ranger/scripts/ranger-admin-entrypoint.sh"
    assert service["environment"]["DB_NAME"] == "ranger-k8s_db"
    assert service["environment"]["DB_HOST"] == "myhost"
    assert service["environment"]["RANGER_ADMIN_PWD"] == "RangerAdmin1"
    assert service["environment"]["JAVA_OPTS"].startswith(
        "-Duser.timezone=UTC0 -Djavax.net.ssl.trustStorePassword="
    )
    assert state_out.get_container(RANGER).plan.to_dict()["checks"]["up"]["http"] == {
        "url": "http://localhost:6080/"
    }
    assert len(state_out.opened_ports) == 1
    assert next(iter(state_out.opened_ports)).port == 6080


def test_usersync_ready(ctx):
    """A ready usersync unit renders all sync environment variables without a port."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_usersync_state())

    plan = state_out.get_container(RANGER).plan.to_dict()
    environment = services(state_out)[RANGER]["environment"]
    assert (
        services(state_out)[RANGER]["command"]
        == "/home/ranger/scripts/ranger-usersync-entrypoint.sh"
    )
    assert set(environment) >= {
        "POLICY_MGR_URL",
        "RANGER_USERSYNC_PWD",
        "SYNC_INTERVAL",
        "SYNC_LDAP_URL",
        "SYNC_LDAP_BIND_DN",
        "SYNC_LDAP_BIND_PASSWORD",
        "SYNC_GROUP_SEARCH_SCOPE",
    }
    assert "checks" not in plan
    assert state_out.opened_ports == frozenset()


def test_usersync_layer_is_yaml_serialisable(ctx):
    """Usersync configuration enums are reduced to YAML-compatible values."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_usersync_state())

    properties = workload_path(state_out, ctx, "/usr/lib/ranger/usersync/install.properties")
    assert "SYNC_LDAP_USER_SEARCH_SCOPE" in properties.read_text()
    assert services(state_out)[RANGER]["environment"]["SYNC_LDAP_USER_SEARCH_SCOPE"] == "sub"
    assert services(state_out)[RANGER]["environment"]["SYNC_GROUP_SEARCH_SCOPE"] == "sub"


def test_reconcile_is_idempotent(ctx):
    """A repeated reconcile preserves both plan and truststore secret revision."""
    with mock_ranger_api():
        first = ctx.run(ctx.on.config_changed(), build_admin_state())
        second = ctx.run(ctx.on.config_changed(), carry_forward(first))

    assert second.get_container(RANGER).plan == first.get_container(RANGER).plan
    first_secret = next(
        secret for secret in first.secrets if secret.label == "truststore-password"
    )
    second_secret = next(
        secret for secret in second.secrets if secret.label == "truststore-password"
    )
    assert second_secret.id == first_secret.id
    assert (
        services(first)[RANGER]["environment"]["JAVA_OPTS"]
        == services(second)[RANGER]["environment"]["JAVA_OPTS"]
    )


@pytest.mark.parametrize(
    ("event", "state"),
    [
        pytest.param(
            lambda ctx, state, relation: ctx.on.config_changed(),
            lambda relation: build_admin_state(database=None),
            id="config-changed",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.secret_changed(next(iter(state.secrets))),
            lambda relation: build_admin_state(database=None),
            id="secret-changed",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.update_status(),
            lambda relation: build_admin_state(database=None),
            id="update-status",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(
                next(item for item in state.relations if item.endpoint == "peer")
            ),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={testing.PeerRelation("peer", peers_data={1: {}})},
            ),
            id="peer-relation-changed",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.pebble_ready(state.get_container(RANGER)),
            lambda relation: build_admin_state(database=None),
            id="ranger-pebble-ready",
        ),
        *[
            pytest.param(
                lambda ctx, state, relation: ctx.on.relation_created(relation),
                lambda relation, endpoint=endpoint: build_admin_state(
                    database=None,
                    extra_relations={
                        testing.Relation(endpoint, remote_app_name=f"{endpoint}-remote")
                    },
                ),
                id=f"{endpoint}-relation-created",
            )
            for endpoint in ("policy", "database", "ldap", "opensearch", "trino-catalog")
        ],
        *[
            pytest.param(
                lambda ctx, state, relation: ctx.on.relation_changed(relation),
                lambda relation, endpoint=endpoint: build_admin_state(
                    database=None,
                    extra_relations={
                        testing.Relation(endpoint, remote_app_name=f"{endpoint}-remote")
                    },
                ),
                id=f"{endpoint}-relation-changed",
            )
            for endpoint in ("policy", "database", "ldap", "opensearch", "trino-catalog")
        ],
        *[
            pytest.param(
                lambda ctx, state, relation: ctx.on.relation_broken(relation),
                lambda relation, endpoint=endpoint: build_admin_state(
                    database=None,
                    extra_relations={
                        testing.Relation(endpoint, remote_app_name=f"{endpoint}-remote")
                    },
                ),
                id=f"{endpoint}-relation-broken",
            )
            for endpoint in ("policy", "database", "ldap", "opensearch", "trino-catalog")
        ],
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={
                    testing.Relation(
                        "ingress",
                        remote_app_name="traefik-k8s",
                        remote_app_data={"ingress": '{"url": "https://ranger.example"}'},
                    )
                },
            ),
            id="ingress-ready",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_broken(relation),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={testing.Relation("ingress", remote_app_name="traefik-k8s")},
            ),
            id="ingress-revoked",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(database=DATABASE_CONNECTION),
            id="database-created",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(database={"endpoints": "postgresql:5432"}),
            id="database-endpoints-changed",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={
                    testing.Relation(
                        "opensearch",
                        remote_app_name="opensearch-k8s",
                        remote_app_data={"username": "ranger", "password": "password"},  # nosec B105
                    )
                },
            ),
            id="opensearch-index-created",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={
                    testing.Relation(
                        "opensearch",
                        remote_app_name="opensearch-k8s",
                        remote_app_data={"endpoints": "opensearch:9200"},
                    )
                },
            ),
            id="opensearch-endpoints-changed",
        ),
        pytest.param(
            lambda ctx, state, relation: ctx.on.relation_changed(relation),
            lambda relation: build_admin_state(
                database=None,
                extra_relations={
                    testing.Relation(
                        "opensearch",
                        remote_app_name="opensearch-k8s",
                        remote_app_data={"username": "ranger"},
                    )
                },
            ),
            id="opensearch-authentication-updated",
        ),
    ],
)
def test_no_defer_on_any_hook(ctx, event, state):
    """Every reconciliation hook returns without deferring when not ready."""
    state = state(None)
    relation = next(
        (
            item
            for item in state.relations
            if item.endpoint not in {"peer", "ingress"} and item.endpoint != "database"
        ),
        None,
    )
    if relation is None:
        relation = next(
            (item for item in state.relations if item.endpoint in {"database", "ingress"}),
            testing.Relation("database", remote_app_name="postgresql-k8s"),
        )

    with mock_ranger_api():
        state_out = ctx.run(event(ctx, state, relation), state)

    assert state_out.deferred == []


def test_empty_plan_when_container_unreachable(ctx):
    """An unreachable workload container reports waiting and renders no layer."""
    state_out = ctx.run(
        ctx.on.config_changed(),
        build_admin_state(container=ranger_container(can_connect=False)),
    )

    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.unit_status == testing.WaitingStatus("waiting for container")


def test_admin_blocked_without_database(ctx):
    """Admin configuration is blocked until PostgreSQL is integrated."""
    state_out = ctx.run(ctx.on.config_changed(), build_admin_state(database=None))

    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.unit_status == testing.BlockedStatus(
        "integrate ranger-k8s with a PostgreSQL database"
    )


def test_admin_waits_for_unready_database(ctx):
    """Admin configuration waits for a related database to publish credentials."""
    state_out = ctx.run(ctx.on.config_changed(), build_admin_state(database={}))

    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.unit_status == testing.WaitingStatus("waiting for database")


def test_database_relation_broken_converges_in_hook(ctx):
    """A departing database relation blocks immediately without deferral."""
    database = testing.Relation(
        "database", remote_app_name="postgresql-k8s", remote_app_data=DATABASE_CONNECTION
    )
    state_out = ctx.run(
        ctx.on.relation_broken(database),
        build_admin_state(database=None, extra_relations={database}),
    )

    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.deferred == []
    assert state_out.unit_status == testing.BlockedStatus(
        "integrate ranger-k8s with a PostgreSQL database"
    )


def test_truststore_secret_created_once_by_leader(ctx):
    """The leader creates one stable truststore secret used by the rendered layer."""
    with mock_ranger_api():
        first = ctx.run(ctx.on.config_changed(), build_admin_state())
        second = ctx.run(ctx.on.config_changed(), carry_forward(first))

    secret = next(secret for secret in first.secrets if secret.label == "truststore-password")
    password = secret.tracked_content["password"]
    assert len([item for item in second.secrets if item.label == "truststore-password"]) == 1
    assert password in services(first)[RANGER]["environment"]["JAVA_OPTS"]
    assert password in services(second)[RANGER]["environment"]["JAVA_OPTS"]


def test_non_leader_waits_for_truststore_secret(ctx):
    """A follower waits until its leader creates the shared truststore secret."""
    state_out = ctx.run(ctx.on.config_changed(), build_admin_state(leader=False))

    assert state_out.get_container(RANGER).plan.to_dict() == {}
    assert state_out.unit_status == testing.WaitingStatus(
        "waiting for leader to create the truststore secret"
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            build_admin_state(config={"charm-function": "invalid"}),
            testing.BlockedStatus("Invalid configuration: charm-function:"),
        ),
        (
            build_admin_state(
                config={"system-users": "secret:missing"},
                extra_secrets=(),
            ),
            testing.BlockedStatus("Invalid configuration: system-users: cannot be resolved;"),
        ),
        (
            build_admin_state(container=ranger_container(can_connect=False)),
            testing.WaitingStatus("waiting for container"),
        ),
        (build_admin_state(database={}), testing.WaitingStatus("waiting for database")),
        (
            build_admin_state(database=None),
            testing.BlockedStatus("integrate ranger-k8s with a PostgreSQL database"),
        ),
        (build_admin_state(leader=False), testing.WaitingStatus("waiting for leader")),
        (build_usersync_state(), testing.ActiveStatus("Status check: UP")),
        (build_admin_state(), testing.MaintenanceStatus("waiting for workload")),
    ],
)
def test_collect_status_ladder(ctx, state, expected):
    """Status collection reports the first applicable non-API health condition."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.collect_unit_status(), state)

    assert state_out.unit_status.name == expected.name
    assert state_out.unit_status.message.startswith(expected.message)


@pytest.mark.parametrize(
    ("check_status", "expected"),
    [
        (CheckStatus.UP, testing.ActiveStatus("Status check: UP")),
        (CheckStatus.DOWN, testing.MaintenanceStatus("Status check: DOWN")),
    ],
)
def test_status_check_up_and_down(ctx, check_status, expected):
    """Admin status reflects its rendered Pebble health check."""
    with mock_ranger_api():
        ready = ctx.run(ctx.on.config_changed(), build_admin_state())
        container = dataclasses.replace(
            ready.get_container(RANGER),
            check_infos={testing.CheckInfo("up", status=check_status)},
        )
        state_out = ctx.run(
            ctx.on.update_status(),
            dataclasses.replace(ready, containers={container}),
        )

    assert state_out.unit_status == expected


def test_restart_action_fails_without_container(ctx):
    """Restart reports an action failure rather than deferring when Pebble is unavailable."""
    with pytest.raises(ActionFailed, match="cannot connect to the ranger container"):
        ctx.run(
            ctx.on.action("restart"),
            build_admin_state(container=ranger_container(can_connect=False)),
        )


def test_ingress_url_reaches_policy_databag(ctx):
    """The external ingress URL is published to related policy consumers."""
    ingress = testing.Relation(
        "ingress",
        remote_app_name="traefik-k8s",
        remote_app_data={"ingress": '{"url": "https://ranger.example"}'},
    )
    policy = testing.Relation("policy", remote_app_name="trino-k8s")
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(extra_relations={ingress, policy}),
        )

    policy_out = next(relation for relation in state_out.relations if relation.id == policy.id)
    assert policy_out.local_app_data["policy_manager_url"] == "https://ranger.example:443"


@pytest.mark.parametrize(
    ("config", "secret_content", "message"),
    [
        (
            {"system-users": "secret:missing"},
            None,
            "Invalid configuration: system-users: cannot be resolved;",
        ),
        (
            {},
            {"admin": "RangerAdmin1"},
            "Invalid configuration: system-users: secret 'system-users' is missing required key",
        ),
        (
            {},
            {"admin": "invalidpassword1", "rangerusersync": "RangerUsersync1"},
            "Invalid configuration: system-users: admin: Password does not match requirements.",
        ),
    ],
)
def test_invalid_system_users_secret_blocks(ctx, config, secret_content, message):
    """Invalid system-user credentials block reconciliation without raising."""
    extra_secrets = ()
    if secret_content is not None:
        secret = testing.Secret(secret_content)
        config = {"system-users": secret.id}
        extra_secrets = (secret,)
    state_out = ctx.run(
        ctx.on.config_changed(),
        build_admin_state(config=config, extra_secrets=extra_secrets),
    )

    assert state_out.unit_status.message.startswith(message)


def test_system_user_passwords_render_literal_characters(ctx):
    """Allowed password characters remain literal in rendered install.properties."""
    secret = testing.Secret(
        {
            "admin": "Pa55word&x<y",
            "rangerusersync": SYSTEM_USERS_SECRET_CONTENT["rangerusersync"],
        }
    )
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(config={"system-users": secret.id}, extra_secrets=(secret,)),
        )

    install_properties = workload_path(
        state_out, ctx, "/usr/lib/ranger/admin/install.properties"
    ).read_text()
    assert "rangerAdmin_password=Pa55word&x<y" in install_properties
    assert "Pa55word&amp;x&lt;y" not in install_properties


def test_secret_changed_uses_latest_system_users_content(ctx):
    """Secret changes render the latest system-user password revision."""
    secret = testing.Secret(
        SYSTEM_USERS_SECRET_CONTENT,
        latest_content={
            "admin": "RangerAdmin2",
            "rangerusersync": "RangerUsersync2",
        },
    )
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.secret_changed(secret),
            build_admin_state(config={"system-users": secret.id}, extra_secrets=(secret,)),
        )

    assert services(state_out)[RANGER]["environment"]["RANGER_ADMIN_PWD"] == "RangerAdmin2"


@pytest.mark.parametrize(
    ("config", "secret_content", "expected"),
    [
        (
            {},
            {"sync-ldap-bind-dn": LDAP_CREDENTIALS_CONTENT["sync-ldap-bind-dn"]},
            "Invalid configuration: ldap-credentials secret is missing required keys: "
            "sync-ldap-bind-password",
        ),
        *[
            (
                {},
                {**LDAP_CREDENTIALS_CONTENT, missing_key: ""},
                f"Invalid configuration: ldap-credentials secret is missing required keys: "
                f"{missing_key}",
            )
            for missing_key in LDAP_CREDENTIALS_CONTENT
        ],
        (
            {"ldap-credentials": "secret:missing"},
            None,
            "Invalid configuration: ldap-credentials: cannot be resolved; ensure the secret ID "
            "is valid and granted to this application.",
        ),
        (
            {"sync-ldap-url": "not-an-ldap-url"},
            LDAP_CREDENTIALS_CONTENT,
            "Invalid configuration: sync-ldap-url: Value incorrectly formatted.",
        ),
    ],
)
def test_usersync_invalid_ldap_credentials_or_config_blocks(ctx, config, secret_content, expected):
    """Invalid LDAP credentials or topology configuration blocks usersync."""
    extra_secrets = ()
    if secret_content is not None:
        secret = testing.Secret(secret_content)
        config = {"ldap-credentials": secret.id, **config}
        extra_secrets = (secret,)

    state_out = ctx.run(
        ctx.on.config_changed(),
        build_usersync_state(
            config=config,
            extra_secrets=extra_secrets,
        ),
    )

    assert state_out.unit_status.message == expected
