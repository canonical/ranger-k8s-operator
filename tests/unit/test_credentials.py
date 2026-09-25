#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Credential ownership, action and recovery tests."""

import time
from unittest import mock

import pytest
from ops import testing
from ops._private.harness import ActionFailed

import ranger_db
from charm import ApiProbe
from exceptions import RangerDatabaseError
from literals import CREDENTIALS_SECRET_LABEL, MANAGED_USERS
from ranger_db import encode_password
from secret_models import validate_password
from tests.unit.helpers import (
    CREDENTIALS_SECRET_CONTENT,
    RANGER,
    build_admin_state,
    build_usersync_state,
    mock_ranger_api,
    workload_path,
)
from utils import generate_password

NEW_PASSWORD = "NewRanger1Password"  # nosec B105


def stored_credentials(state):
    """Return the contents of the charm-owned credentials secret.

    Args:
        state: The output state from a Context.run call.

    Returns:
        The stored credentials mapping.
    """
    secret = state.get_secret(label=CREDENTIALS_SECRET_LABEL)
    return secret.latest_content or secret.tracked_content


def run_set_password(ctx, state, **params):
    """Run the set-password action against a state.

    Args:
        ctx: Scenario context.
        state: The input state.
        **params: Action parameters.

    Returns:
        The output state.
    """
    return ctx.run(ctx.on.action("set-password", params=params), state)


def test_leader_creates_credentials(ctx):
    """An admin leader generates all four passwords on its first reconciliation."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state(credentials=None))

    credentials = stored_credentials(state_out)
    assert sorted(credentials) == sorted(MANAGED_USERS)
    assert len(set(credentials.values())) == len(MANAGED_USERS)
    for password in credentials.values():
        assert validate_password(password) == password


def test_existing_credentials_are_reused(ctx):
    """An existing credentials secret is neither rewritten nor regenerated."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state())

    assert stored_credentials(state_out) == CREDENTIALS_SECRET_CONTENT


def test_missing_users_are_backfilled(ctx):
    """A user added to MANAGED_USERS later is generated without disturbing the rest."""
    partial = {
        user: password
        for user, password in CREDENTIALS_SECRET_CONTENT.items()
        if user != "rangertagsync"
    }

    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state(credentials=partial))

    credentials = stored_credentials(state_out)
    assert sorted(credentials) == sorted(MANAGED_USERS)
    assert {user: credentials[user] for user in partial} == partial
    assert validate_password(credentials["rangertagsync"])


def test_non_leader_waits_for_a_complete_secret(ctx):
    """A non-leader reports no credentials rather than an incomplete mapping."""
    partial = {"admin": CREDENTIALS_SECRET_CONTENT["admin"]}

    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.config_changed(),
            build_admin_state(leader=False, credentials=partial),
        )

    assert state_out.unit_status.name == "waiting"


def test_key_and_tag_users_get_their_own_passwords(ctx):
    """Rendered install.properties seeds each internal user independently."""
    with mock_ranger_api():
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state())

    install_properties = workload_path(
        state_out, ctx, "/usr/lib/ranger/admin/install.properties"
    ).read_text()
    assert "rangerAdmin_password=RangerAdmin1" in install_properties
    assert "keyadmin_password=RangerKeyadmin1" in install_properties
    assert "rangerTagsync_password=RangerTagsync1" in install_properties
    assert "rangerUsersync_password=RangerUsersync1" in install_properties


def test_get_password_returns_every_stored_password(ctx):
    """The action returns the passwords held in the credentials secret."""
    ctx.run(ctx.on.action("get-password"), build_admin_state())

    assert ctx.action_results == CREDENTIALS_SECRET_CONTENT


def test_get_password_returns_only_stored_keys(ctx):
    """Credentials missing keys after an upgrade neither block nor fail the action."""
    credentials = {
        "admin": CREDENTIALS_SECRET_CONTENT["admin"],
        "rangerusersync": CREDENTIALS_SECRET_CONTENT["rangerusersync"],
    }
    with mock_ranger_api():
        state_out = ctx.run(
            ctx.on.collect_unit_status(), build_admin_state(credentials=credentials)
        )
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)

    ctx.run(ctx.on.action("get-password"), build_admin_state(credentials=credentials))
    assert ctx.action_results == credentials


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (build_admin_state(leader=False), "run this action on the leader unit"),
        (build_usersync_state(), "run this action on the Ranger admin application"),
    ],
)
def test_get_password_refuses_elsewhere(ctx, state, message):
    """The action only runs on the leader unit of the admin application."""
    with pytest.raises(ActionFailed, match=message):
        ctx.run(ctx.on.action("get-password"), state)


def test_rotating_admin_uses_a_self_service_change(ctx):
    """Rotating admin presents the stored password as the old one."""
    with mock_ranger_api(passwords=dict(CREDENTIALS_SECRET_CONTENT)) as client:
        state_out = run_set_password(ctx, build_admin_state(), username="admin")

    change = next(call for call in client.calls if call[0] == "change_own_password")
    assert change[2] == "admin"
    assert change[3] == CREDENTIALS_SECRET_CONTENT["admin"]
    new_password = stored_credentials(state_out)["admin"]
    assert new_password == change[4]
    assert new_password != CREDENTIALS_SECRET_CONTENT["admin"]
    assert ctx.action_results["result"] == "changed"


def test_setting_usersync_uses_the_administrator(ctx):
    """Changing another internal user needs no old password and warns about usersync."""
    with mock_ranger_api(passwords=dict(CREDENTIALS_SECRET_CONTENT)) as client:
        state_out = run_set_password(
            ctx, build_admin_state(), username="rangerusersync", password=NEW_PASSWORD
        )

    assert ("set_user_password", "rangerusersync", NEW_PASSWORD) in client.calls
    assert stored_credentials(state_out)["rangerusersync"] == NEW_PASSWORD
    assert "usersync-credentials" in ctx.action_results["note"]


def test_keyadmin_changes_its_own_password(ctx):
    """Ranger refuses administrator access to keyadmin, so it authenticates as itself."""
    with mock_ranger_api(passwords=dict(CREDENTIALS_SECRET_CONTENT)) as client:
        run_set_password(ctx, build_admin_state(), username="keyadmin")

    change = next(call for call in client.calls if call[0] == "change_own_password")
    assert change[2] == "keyadmin"
    assert ("keyadmin", CREDENTIALS_SECRET_CONTENT["keyadmin"]) in [
        auth for _, auth in client.clients
    ]
    assert not [call for call in client.calls if call[0] == "set_user_password"]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"username": "admin", "override": True}, "override requires password"),
        ({"username": "postgres"}, "username must be one of"),
    ],
)
def test_set_password_rejects_invalid_modes(ctx, params, message):
    """Mode selection is validated before Ranger is contacted."""
    with mock_ranger_api() as client:
        with pytest.raises(ActionFailed, match=message):
            run_set_password(ctx, build_admin_state(), **params)

    assert not [call for call in client.calls if call[0] != "authenticate"]


def test_set_password_validates_complexity(ctx):
    """An operator-supplied password is validated client-side."""
    with mock_ranger_api() as client:
        with pytest.raises(ActionFailed, match="Password does not match requirements."):
            run_set_password(
                ctx,
                build_admin_state(),
                username="admin",
                password="short",  # nosec B106
            )

    assert not [call for call in client.calls if call[0] != "authenticate"]


def test_override_refused_while_the_record_is_valid(ctx):
    """Override is refused when the charm can still authenticate as the user."""
    with mock_ranger_api(passwords=dict(CREDENTIALS_SECRET_CONTENT)) as client:
        with pytest.raises(ActionFailed, match="the charm's record is already valid"):
            run_set_password(
                ctx,
                build_admin_state(),
                username="admin",
                password=NEW_PASSWORD,
                override=True,
            )

    assert not [call for call in client.calls if call[0] != "authenticate"]


def test_override_records_a_password_ranger_already_accepts(ctx):
    """An out-of-band change is recorded without touching the database."""
    passwords = {**CREDENTIALS_SECRET_CONTENT, "admin": NEW_PASSWORD}
    with mock_ranger_api(passwords=passwords):
        with mock.patch("charm.ranger_db.force_reset") as force_reset:
            state_out = run_set_password(
                ctx,
                build_admin_state(),
                username="admin",
                password=NEW_PASSWORD,
                override=True,
            )

    force_reset.assert_not_called()
    assert ctx.action_results["result"] == "recorded"
    assert stored_credentials(state_out)["admin"] == NEW_PASSWORD


def test_override_force_resets_an_unknown_password(ctx):
    """A password nobody knows is rewritten in PostgreSQL and then recorded."""
    with mock_ranger_api(passwords={}):
        with mock.patch("charm.ranger_db.force_reset") as force_reset:
            state_out = run_set_password(
                ctx,
                build_admin_state(),
                username="admin",
                password=NEW_PASSWORD,
                override=True,
            )

    connection, login_id, password = force_reset.call_args.args
    assert connection["dbname"] == "ranger-k8s_db"
    assert (login_id, password) == ("admin", NEW_PASSWORD)
    assert ctx.action_results["result"] == "force-reset"
    assert stored_credentials(state_out)["admin"] == NEW_PASSWORD


def test_rejection_is_remembered_between_hooks(ctx):
    """Ranger locks accounts out, so a rejection is recorded rather than re-probed."""
    peer = testing.PeerRelation("peer")
    with mock_ranger_api(probe=ApiProbe.REJECTED):
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state(extra_relations={peer}))

    peer_out = next(relation for relation in state_out.relations if relation.id == peer.id)
    assert peer_out.local_unit_data["credential-rejected-at"]


def test_recent_rejection_skips_the_probe(ctx):
    """A recent rejection keeps the unit blocked without hammering Ranger."""
    peer = testing.PeerRelation(
        "peer", local_unit_data={"credential-rejected-at": str(time.time())}
    )
    with mock_ranger_api() as client:
        state_out = ctx.run(ctx.on.config_changed(), build_admin_state(extra_relations={peer}))

    assert not [call for call in client.calls if call[0] == "authenticate"]
    assert isinstance(state_out.unit_status, testing.BlockedStatus)


def test_override_refuses_while_ranger_is_unreachable(ctx):
    """An unreachable Ranger must never trigger a database force reset."""
    with mock_ranger_api(probe=ApiProbe.UNREACHABLE):
        with mock.patch("charm.ranger_db.force_reset") as force_reset:
            with pytest.raises(ActionFailed, match="Ranger is unreachable"):
                run_set_password(
                    ctx,
                    build_admin_state(),
                    username="admin",
                    password=NEW_PASSWORD,
                    override=True,
                )

    force_reset.assert_not_called()


def test_override_requires_the_database_relation(ctx):
    """A force reset without PostgreSQL asks for the integration."""
    with mock_ranger_api(passwords={}):
        with pytest.raises(ActionFailed, match="integrate ranger-k8s with a PostgreSQL database"):
            run_set_password(
                ctx,
                build_admin_state(database=None),
                username="admin",
                password=NEW_PASSWORD,
                override=True,
            )


def test_set_password_fails_when_ranger_is_unreachable(ctx):
    """An unreachable workload leaves the stored password untouched."""
    with mock_ranger_api(failure="get_user"):
        with pytest.raises(ActionFailed):
            state_out = run_set_password(ctx, build_admin_state(), username="admin")

    state_out = ctx.run(ctx.on.action("get-password"), build_admin_state())
    assert ctx.action_results == CREDENTIALS_SECRET_CONTENT
    assert state_out is not None


def test_usersync_blocks_without_credentials(ctx):
    """A usersync application without its secret says where to obtain one."""
    state_out = ctx.run(
        ctx.on.collect_unit_status(),
        build_usersync_state(config={"usersync-credentials": ""}),
    )

    assert state_out.unit_status.name == "blocked"
    assert "usersync-credentials is required" in state_out.unit_status.message


def test_admin_rejects_usersync_credentials(ctx):
    """The usersync option belongs on the usersync application."""
    secret = testing.Secret({"rangerusersync": "RangerUsersync1"})
    state_out = ctx.run(
        ctx.on.collect_unit_status(),
        build_admin_state(config={"usersync-credentials": secret.id}, extra_secrets=(secret,)),
    )

    assert state_out.unit_status.name == "blocked"
    assert "only valid when charm-function is usersync" in state_out.unit_status.message


def test_encode_password_matches_ranger():
    """The digest reproduces the values Ranger stores for known passwords."""
    assert (
        encode_password("RangerAdmin1", "admin")
        == "963bec8ce017f85df3e0d2fdae3718718441f3cf3cd5ad7860a8a9bfa31ccad0"
    )
    assert (
        encode_password("RangerAdmin1", "keyadmin")
        == "1dffc772198532a445b6e7ee960a6c4e0f0ca781ccd2256c74a3876d1bab674c"
    )


def test_generated_passwords_are_always_valid():
    """Generation never produces a password Ranger would reject."""
    for _ in range(50):
        password = generate_password()
        assert validate_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        "Valid123\n",
        "Valid\r123",
        "Valid\t123",
        'Valid"123',
        "Valid'123",
        "Valid\\123",
        "Valid`123",
        "valid1234",
        "VALID1234",
        "ValidPass",
        "Valid12",
    ],
)
def test_validate_password_rejects(password):
    """Weak passwords and characters that would break install.properties are refused."""
    with pytest.raises(ValueError, match="Password does not match requirements."):
        validate_password(password)


def fake_connection(rowcount):
    """Build a psycopg2 connection whose cursor reports a row count.

    Args:
        rowcount: The number of rows the update should report.

    Returns:
        A mocked connection and its cursor.
    """
    conn = mock.MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = rowcount
    return conn, cursor


def test_force_reset_writes_the_digest():
    """The recovery write stores the digest Ranger expects for the user.

    Returns:
        None.
    """
    conn, cursor = fake_connection(1)
    with mock.patch("ranger_db.psycopg2.connect", return_value=conn):
        ranger_db.force_reset({"dbname": "ranger"}, "admin", "RangerAdmin1")

    assert cursor.execute.call_args.args[1] == {
        "password": encode_password("RangerAdmin1", "admin"),
        "login_id": "admin",
    }
    conn.close.assert_called_once()


def test_force_reset_requires_a_single_row():
    """An update that does not hit exactly one internal user is rolled back."""
    conn, _ = fake_connection(0)
    with mock.patch("ranger_db.psycopg2.connect", return_value=conn):
        with pytest.raises(RangerDatabaseError, match="found 0"):
            ranger_db.force_reset({"dbname": "ranger"}, "admin", "RangerAdmin1")

    conn.rollback.assert_called_once()


def test_reconciliation_waits_for_the_leader(ctx):
    """A follower waits for its leader to create the credentials secret."""
    truststore = testing.Secret({"password": "truststore"}, label="truststore-password")  # nosec B105
    state_out = ctx.run(
        ctx.on.collect_unit_status(),
        build_admin_state(leader=False, credentials=None, extra_secrets=(truststore,)),
    )

    assert state_out.unit_status == testing.WaitingStatus(
        "waiting for leader to create the credentials secret"
    )
    assert state_out.get_container(RANGER).plan.to_dict() == {}
