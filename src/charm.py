#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service."""

import logging
import subprocess  # nosec B404
import time
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires, OpenSearchRequires
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from charms.trino_k8s.v0.trino_catalog import TrinoCatalogRequirer
from ops.charm import CollectStatusEvent
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    SecretNotFoundError,
    WaitingStatus,
)
from ops.pebble import CheckStatus, ExecError
from pydantic import ValidationError

import ranger_db
from credentials import CredentialStore
from exceptions import RangerDatabaseError, RelationNotReady
from literals import (
    ADMIN_ENTRYPOINT,
    ADMIN_USER,
    APPLICATION_PORT,
    KEYADMIN_USER,
    LDAP_BIND_CREDENTIAL_CONFIG_KEYS,
    LDAP_TOPOLOGY_CONFIG_KEYS,
    LOCALHOST_URL,
    LOG_FILES,
    MANAGED_USERS,
    METRICS_PORT,
    SUPPRESS_DEBUG_LOGS,
    TAGSYNC_USER,
    TRUSTSTORE_SECRET_LABEL,
    USERSYNC_CONFIG_MAPPING,
    USERSYNC_ENTRYPOINT,
    USERSYNC_USER,
)
from ranger_client import RangerAPIClient, RangerAPIError, RangerAuthenticationError
from relations.ldap import LDAPRelationHandler
from relations.opensearch import OpensearchRelationHandler
from relations.postgres import PostgresRelationHandler
from relations.provider import RangerProvider
from relations.trino import TrinoCatalogRelationHandler
from secret_models import (
    LdapCredentials,
    SecretValidationError,
    UsersyncCredentials,
    validate_password,
)
from structured_config import CharmConfig
from utils import content_hash, generate_password, log_event_handler, render

logger = logging.getLogger(__name__)


class ApiProbe(Enum):
    """Represent the configured credentials' Ranger API authentication result."""

    OK = "ok"
    REJECTED = "rejected"
    UNREACHABLE = "unreachable"


class RangerK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attributes:
        config_type: The charm structured config.
    """

    config_type = CharmConfig
    API_PROBE_TIMEOUT = 5
    # Ranger locks an account out after 5 failed logins within 5 minutes, so a rejected
    # probe is remembered rather than repeated on every hook.
    PROBE_BACKOFF = 300
    PROBE_REJECTED_AT = "credential-rejected-at"

    def __init__(self, *args):
        """Construct.

        Args:
            args: Ignore.
        """
        super().__init__(*args)
        self._probe_result: Optional[ApiProbe] = None
        self._usersync_credentials: Optional[UsersyncCredentials] = None
        self._ldap_credentials: Optional[LdapCredentials] = None
        self._configure_logging()
        self.name = "ranger"
        self.credentials = CredentialStore(self)

        self.postgres_relation = DatabaseRequires(
            self,
            relation_name="database",
            database_name=PostgresRelationHandler.DB_NAME,
            extra_user_roles="admin",
        )
        self.postgres_relation_handler = PostgresRelationHandler(self)
        self.provider = RangerProvider(self)
        self.ldap = LDAPRelationHandler(self)
        self.opensearch_relation = OpenSearchRequires(
            self,
            relation_name="opensearch",
            index="ranger_audits",
            extra_user_roles="admin",
        )
        self.opensearch_relation_handler = OpensearchRelationHandler(self)
        self.trino_catalog_requirer = TrinoCatalogRequirer(self)
        self.trino_catalog_handler = TrinoCatalogRelationHandler(self)
        self.ingress = IngressPerAppRequirer(
            self,
            relation_name="ingress",
            port=APPLICATION_PORT,
            strip_prefix=True,
            redirect_https=True,
            scheme="http",
        )

        self.framework.observe(self.on.config_changed, self._reconcile_hook)
        self.framework.observe(self.on.secret_changed, self._reconcile_hook)
        self.framework.observe(self.on.update_status, self._reconcile_hook)
        self.framework.observe(self.on.peer_relation_changed, self._reconcile_hook)
        self.framework.observe(self.on.ranger_pebble_ready, self._reconcile_hook)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(self.on.get_password_action, self._on_get_password)
        self.framework.observe(self.on.set_password_action, self._on_set_password)

        for endpoint in ("policy", "database", "ldap", "opensearch", "trino-catalog"):
            self.framework.observe(self.on[endpoint].relation_created, self._reconcile_hook)
            self.framework.observe(self.on[endpoint].relation_changed, self._reconcile_hook)
            self.framework.observe(self.on[endpoint].relation_broken, self._reconcile_hook)
        self.framework.observe(self.ingress.on.ready, self._reconcile_hook)
        self.framework.observe(self.ingress.on.revoked, self._reconcile_hook)
        self.framework.observe(self.postgres_relation.on.database_created, self._reconcile_hook)
        self.framework.observe(self.postgres_relation.on.endpoints_changed, self._reconcile_hook)
        self.framework.observe(self.opensearch_relation.on.index_created, self._reconcile_hook)
        self.framework.observe(self.opensearch_relation.on.endpoints_changed, self._reconcile_hook)
        self.framework.observe(
            self.opensearch_relation.on.authentication_updated, self._reconcile_hook
        )

        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": "/service/metrics/prometheus",
                    "static_configs": [{"targets": [f"*:{METRICS_PORT}"]}],
                }
            ],
            refresh_event=self.on.config_changed,
        )
        self.log_proxy = LogProxyConsumer(self, log_files=LOG_FILES, relation_name="log-proxy")
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

    @property
    def usersync_credentials(self) -> Optional[UsersyncCredentials]:
        """Resolve the usersync-credentials secret once for the current hook.

        Returns:
            The validated usersync credentials, or None when no secret is configured.

        Raises:
            SecretValidationError: If the configured secret is unavailable or invalid.
        """
        secret_id = self.config["usersync-credentials"]
        if not secret_id:
            return None
        if self._usersync_credentials is None:
            self._usersync_credentials = self._resolve_secret(
                "usersync-credentials", secret_id, UsersyncCredentials
            )
        return self._usersync_credentials

    @property
    def ldap_credentials(self) -> Optional[LdapCredentials]:
        """Resolve the optional ldap-credentials secret once for the current hook.

        Returns:
            The validated LDAP credentials, or None when no secret is configured.

        Raises:
            SecretValidationError: If the configured secret is unavailable or invalid.
        """
        secret_id = self.config["ldap-credentials"]
        if not secret_id:
            return None
        if self._ldap_credentials is None:
            self._ldap_credentials = self._resolve_secret(
                "ldap-credentials", secret_id, LdapCredentials
            )
        return self._ldap_credentials

    def _resolve_secret(self, option, secret_id, model_type):
        """Resolve and validate a secret payload.

        Args:
            option: Hyphenated Juju configuration option naming the secret.
            secret_id: Juju secret ID to resolve.
            model_type: Pydantic model used to validate the secret payload.

        Returns:
            A validated secret model.

        Raises:
            SecretValidationError: If the secret is unavailable or its payload is invalid.
        """
        if not secret_id:
            raise SecretValidationError(
                f"Invalid configuration: {option}: must be a Juju secret ID granted "
                "to this application."
            )
        try:
            content = self.model.get_secret(id=secret_id).get_content(refresh=True)
        except ops.ModelError as err:
            raise SecretValidationError(
                f"Invalid configuration: {option}: cannot be resolved; ensure the secret ID "
                "is valid and granted to this application."
            ) from err
        try:
            return model_type(**content)
        except ValidationError as err:
            raise SecretValidationError(self._format_secret_validation_error(option, err)) from err

    @staticmethod
    def _format_secret_validation_error(option: str, error: ValidationError) -> str:
        """Format a secret payload validation error without including secret values.

        Args:
            option: Hyphenated Juju configuration option naming the secret.
            error: Pydantic validation error for the secret payload.

        Returns:
            An actionable secret validation message.
        """
        errors = error.errors()
        missing_keys = [
            str(validation_error["loc"][-1])
            for validation_error in errors
            if validation_error["msg"] == "field required"
        ]
        if missing_keys:
            if option == "ldap-credentials":
                return (
                    "Invalid configuration: ldap-credentials secret is missing required keys: "
                    + ", ".join(missing_keys)
                )
            key = missing_keys[0]
            return (
                f"Invalid configuration: {option}: secret '{option}' is missing "
                f"required key '{key}'."
            )
        validation_error = errors[0]
        key = validation_error["loc"][-1]
        return f"Invalid configuration: {option}: {key}: {validation_error['msg']}"

    def resolve_policy_manager_url(self) -> Optional[str]:
        """Resolve the policy manager URL for the current charm function.

        Returns:
            Full URL string, or None for usersync when policy-mgr-url is not configured.
        """
        override = self.config["policy-mgr-url"]
        if override:
            return override
        if self.config["charm-function"].value == "usersync":
            return None
        ingress_url = self.ingress.url
        if ingress_url:
            parsed = urlparse(ingress_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return f"{parsed.scheme}://{parsed.hostname}:{port}"
        return f"http://{self.app.name}.{self.model.name}.svc.cluster.local:{APPLICATION_PORT}"

    @staticmethod
    def _configure_logging():
        """Suppress noisy third-party HTTP debug logs when enabled."""
        if SUPPRESS_DEBUG_LOGS:
            logging.getLogger("apache_ranger").setLevel(logging.WARNING)
            logging.getLogger("urllib3").setLevel(logging.WARNING)

    def _reconcile_hook(self, event):
        """Route any observed hook through the single reconciler.

        Args:
            event: The triggering Juju event (unused; state is read from the model).
        """
        self._reconcile()

    def _on_collect_unit_status(self, event: CollectStatusEvent):  # noqa: C901
        """Derive terminal unit status from the current model and workload health.

        Args:
            event: The collect-unit-status event to add derived statuses to.
        """
        try:
            cfg = self.config
        except ValidationError as err:
            event.add_status(BlockedStatus(self._format_validation_error(err)))
            return

        function = cfg["charm-function"].value
        try:
            _ = self.usersync_credentials
            _ = self.ldap_credentials
        except SecretValidationError as err:
            event.add_status(BlockedStatus(str(err)))
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.add_status(WaitingStatus("waiting for container"))
            return

        try:
            self._validate_relations(function)
        except RelationNotReady as err:
            event.add_status(WaitingStatus(str(err)))
            return
        except ValueError as err:
            event.add_status(BlockedStatus(str(err)))
            return

        if function == "admin" and self._ensure_truststore_password() is None:
            event.add_status(WaitingStatus("waiting for leader to create the truststore secret"))
            return

        if function == "admin" and self.credentials.ensure() is None:
            event.add_status(WaitingStatus("waiting for leader to create the credentials secret"))
            return

        probe = self._probe_credentials(function)
        if probe is ApiProbe.REJECTED:
            event.add_status(BlockedStatus(self._authentication_failure_message(function)))
            return

        if probe is ApiProbe.OK and function == "admin" and self.unit.is_leader():
            has_trino_service = self.trino_catalog_handler.has_trino_service(
                self._ranger_api_client()
            )
            if has_trino_service is False:
                event.add_status(BlockedStatus("Trino service not found in Ranger"))
                return

        if function == "usersync":
            event.add_status(ActiveStatus("Status check: UP"))
            return

        try:
            check = container.get_check("up")
        except ModelError:
            event.add_status(MaintenanceStatus("waiting for workload"))
            return
        if check.status != CheckStatus.UP:
            event.add_status(MaintenanceStatus("Status check: DOWN"))
            return
        event.add_status(ActiveStatus("Status check: UP"))

    @log_event_handler(logger)
    def _on_restart(self, event):
        """Restart Ranger through the Pebble API.

        Args:
            event: The restart action event.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.fail("cannot connect to the ranger container")
            return
        self.unit.status = MaintenanceStatus("restarting ranger")
        container.restart(self.name)
        event.set_results({"result": "ranger successfully restarted"})

    def _is_admin_leader(self, event) -> bool:
        """Check that a credential action is running where it can be serviced.

        Args:
            event: The action event to fail when it cannot be serviced.

        Returns:
            Whether the action may proceed.
        """
        if not self.unit.is_leader():
            event.fail("run this action on the leader unit")
            return False
        try:
            function = self.config["charm-function"].value
        except ValidationError:
            event.fail("the charm configuration is invalid")
            return False
        if function != "admin":
            event.fail("run this action on the Ranger admin application")
            return False
        return True

    @log_event_handler(logger)
    def _on_get_password(self, event):
        """Return the Ranger internal-user passwords the charm holds.

        Args:
            event: The get-password action event.
        """
        if not self._is_admin_leader(event):
            return
        credentials = self.credentials.read()
        if not credentials:
            event.fail("the charm has not created its Ranger credentials yet")
            return
        event.set_results(
            {user: credentials[user] for user in MANAGED_USERS if credentials.get(user)}
        )

    @log_event_handler(logger)
    def _on_set_password(self, event):
        """Change a Ranger internal-user password or reconcile the charm's record.

        Args:
            event: The set-password action event.
        """
        if not self._is_admin_leader(event):
            return
        username = event.params["username"]
        password = event.params.get("password")
        rotate = event.params.get("rotate", False)
        override = event.params.get("override", False)

        if username not in MANAGED_USERS:
            event.fail(f"username must be one of {', '.join(MANAGED_USERS)}")
            return
        if rotate and password:
            event.fail("rotate and password are mutually exclusive")
            return
        if override and not password:
            event.fail("override requires password")
            return
        if not rotate and not password:
            event.fail("provide password or rotate=true")
            return

        new_password = password or generate_password()
        try:
            validate_password(new_password)
        except ValueError as err:
            event.fail(str(err))
            return

        if override:
            self._override_password(event, username, new_password)
            return

        try:
            self._change_password(username, new_password)
        except RangerAPIError as err:
            event.fail(str(err))
            return
        self.credentials.set(username, new_password)
        self._clear_probe_backoff()
        results = {"result": "changed", "username": username}
        if username == USERSYNC_USER:
            results["note"] = "update the usersync application's usersync-credentials secret"
        event.set_results(results)

    def _change_password(self, username: str, new_password: str) -> None:
        """Change a Ranger internal user's password through the API.

        Args:
            username: The Ranger internal user to change.
            new_password: The password to apply.

        Raises:
            RangerAPIError: If the charm holds no usable password for a self-service change,
                or if Ranger rejects the change.
        """
        if username in (ADMIN_USER, KEYADMIN_USER):
            # Ranger refuses to let an administrator modify a key administrator, and a
            # self-service change is the only path that works for both users.
            current = self.credentials.get(username)
            if not current:
                raise RangerAPIError(
                    f"No {username} password is recorded; rerun with override=true and the "
                    "password Ranger holds."
                )
            client = self._api_client_as(username, current)
            client.change_own_password(
                client.get_user(username)["id"], username, current, new_password
            )
            return
        client = self._ranger_api_client()
        client.set_user_password(client.get_user(username), new_password)

    def _override_password(self, event, username: str, new_password: str) -> None:
        """Record a password applied out of band, resetting Ranger when it does not match.

        Args:
            event: The set-password action event.
            username: The Ranger internal user to reconcile.
            new_password: The password the operator supplied.
        """
        current = self.credentials.get(username)
        if current and self._probe_password(username, current) is ApiProbe.OK:
            event.fail("the charm's record is already valid; omit override to change the password")
            return
        probe = self._probe_password(username, new_password)
        if probe is ApiProbe.OK:
            self.credentials.set(username, new_password)
            self._clear_probe_backoff()
            event.set_results({"result": "recorded", "username": username})
            return
        if probe is ApiProbe.UNREACHABLE:
            event.fail("Ranger is unreachable; retry once the workload is running")
            return

        connection = self.postgres_relation_handler.get_connection()
        if connection is None:
            event.fail("integrate ranger-k8s with a PostgreSQL database to force-reset")
            return
        try:
            ranger_db.force_reset(connection, username, new_password)
        except RangerDatabaseError as err:
            event.fail(str(err))
            return
        self.credentials.set(username, new_password)
        self._clear_probe_backoff()
        event.set_results({"result": "force-reset", "username": username})

    def _probe_password(self, username: str, password: str) -> ApiProbe:
        """Ask Ranger for a verdict on a password for a managed user.

        Args:
            username: The Ranger internal user to authenticate as.
            password: The password to authenticate with.

        Returns:
            Whether Ranger accepted, rejected, or could not be reached.
        """
        try:
            self._api_client_as(username, password).authenticate(self.API_PROBE_TIMEOUT)
        except RangerAuthenticationError:
            return ApiProbe.REJECTED
        except RangerAPIError:
            return ApiProbe.UNREACHABLE
        return ApiProbe.OK

    def _ensure_truststore_password(self):
        """Return the stable truststore password backed by an app Juju secret.

        Returns:
            The password, or None when a non-leader unit cannot yet read the
            leader-created secret.
        """
        try:
            return (
                self.model.get_secret(label=TRUSTSTORE_SECRET_LABEL)
                .get_content(refresh=True)
                .get("password")
            )
        except SecretNotFoundError:
            pass
        if not self.unit.is_leader():
            return None
        password = generate_password()
        self.app.add_secret({"password": password}, label=TRUSTSTORE_SECRET_LABEL)
        return password

    def set_truststore_password(self, container, truststore_pwd):
        """Update the Java truststore password.

        Args:
            container: The workload container.
            truststore_pwd: The desired truststore password.
        """
        command = [
            "keytool",
            "-storepass",
            "changeit",
            "-storepasswd",
            "-new",
            truststore_pwd,
            "-cacerts",
        ]
        try:
            container.exec(command).wait_output()
        except (subprocess.CalledProcessError, ExecError) as error:
            if error.stderr and (
                "password was incorrect" in error.stderr or "Warning" in error.stderr
            ):
                return
            logger.debug("Unable to update truststore password %s", error.stderr)

    def _reconcile_admin(self, container, truststore_pwd, credentials):
        """Prepare Ranger Admin configuration and truststore state.

        Args:
            container: The workload container.
            truststore_pwd: The Java truststore password.
            credentials: The stored Ranger internal-user passwords.

        Returns:
            The Ranger Admin entrypoint and Pebble environment.
        """
        db_conn = self.postgres_relation_handler.get_connection()
        opensearch = self.opensearch_relation_handler.gather_connection()
        certificate = self.opensearch_relation_handler.gather_certificate()
        self.set_truststore_password(container, truststore_pwd)
        self.opensearch_relation_handler.reconcile_index_mapping(opensearch)
        self.opensearch_relation_handler.reconcile_truststore_certificate(
            container, certificate, truststore_pwd
        )
        context = {
            "DB_NAME": db_conn["dbname"],
            "DB_HOST": db_conn["host"],
            "DB_PORT": db_conn["port"],
            "DB_USER": db_conn["user"],
            "DB_PWD": db_conn["password"],
            "OPENSEARCH_INDEX": opensearch.get("index"),
            "OPENSEARCH_HOST": opensearch.get("host"),
            "OPENSEARCH_PORT": opensearch.get("port"),
            "OPENSEARCH_PWD": opensearch.get("password"),
            "OPENSEARCH_USER": opensearch.get("username"),
            "OPENSEARCH_ENABLED": opensearch.get("is_enabled"),
            "OPENSEARCH_CERT_HASH": content_hash(certificate or ""),
            "RANGER_ADMIN_PWD": credentials.get(ADMIN_USER),
            "JAVA_OPTS": (
                f"-Duser.timezone=UTC0 -Djavax.net.ssl.trustStorePassword={truststore_pwd}"
            ),
            "RANGER_USERSYNC_PWD": credentials.get(USERSYNC_USER),
            "RANGER_KEYADMIN_PWD": credentials.get(KEYADMIN_USER),
            "RANGER_TAGSYNC_PWD": credentials.get(TAGSYNC_USER),
        }
        config = render("admin-config.jinja", context)
        container.push("/usr/lib/ranger/admin/install.properties", config, make_dirs=True)
        return ADMIN_ENTRYPOINT, context

    @staticmethod
    def _render_config_value(value):
        """Render a configuration value for install.properties and the Pebble layer.

        Args:
            value: Configuration value to render.

        Returns:
            The rendered value, or an empty string when unset.
        """
        if value is None:
            return ""
        if isinstance(value, Enum):
            return value.value
        return value

    def _reconcile_usersync(self, container):
        """Prepare Ranger Usersync install.properties file.

        Args:
            container: The workload container.

        Returns:
            The Ranger Usersync entrypoint and Pebble environment.
        """
        ldap = self.ldap.relation_values()
        ldap_credentials = self.ldap_credentials
        usersync_credentials = self.usersync_credentials
        context = {}
        for config_key, ranger_property in USERSYNC_CONFIG_MAPPING.items():
            value = ldap.get(config_key)
            if config_key in LDAP_BIND_CREDENTIAL_CONFIG_KEYS:
                if not value:
                    value = getattr(ldap_credentials, config_key) if ldap_credentials else None
            elif config_key in LDAP_TOPOLOGY_CONFIG_KEYS:
                if not value:
                    value = self.config[config_key]
            elif value is None:
                value = self.config[config_key]
            context[ranger_property] = self._render_config_value(value)
        context.update(
            {
                "POLICY_MGR_URL": self.resolve_policy_manager_url(),
                "RANGER_USERSYNC_PWD": (
                    usersync_credentials.rangerusersync if usersync_credentials else None
                ),
            }
        )
        config = render("ranger-usersync-config.jinja", context)
        container.push("/usr/lib/ranger/usersync/install.properties", config, make_dirs=True)
        return USERSYNC_ENTRYPOINT, context

    def _validate_relations(self, function):
        """Validate relations required by the selected charm function.

        Args:
            function: The selected charm function.

        Raises:
            ValueError: If a required relation or configuration is invalid.
        """
        if function == "admin":
            self.postgres_relation_handler.validate()
        if function == "usersync":
            self.ldap.validate()
        if self.model.relations["opensearch"] and function != "admin":
            raise ValueError("Only Ranger admin can relate to OpenSearch.")

    def _ranger_api_client(self) -> RangerAPIClient:
        """Create an API client for the local Ranger Admin service.

        Returns:
            A Ranger API client using the stored administrator credentials.
        """
        return self._api_client_as(ADMIN_USER, self.credentials.get(ADMIN_USER) or "")

    @staticmethod
    def _authentication_failure_message(function) -> str:
        """Build the blocked message for credentials Ranger rejected.

        Args:
            function: The selected charm function.

        Returns:
            An actionable authentication failure message.
        """
        if function == "usersync":
            return (
                f"Ranger authentication failed for {USERSYNC_USER}. Run the get-password action "
                "on the Ranger admin application and update the usersync-credentials secret."
            )
        return (
            f"Ranger authentication failed for {ADMIN_USER}. Run the set-password action with "
            "override=true on the leader unit to reconcile the charm's record."
        )

    def _api_client_as(self, username: str, password: str) -> RangerAPIClient:
        """Create an API client for the local Ranger Admin service as a managed user.

        Args:
            username: The Ranger internal user to authenticate as.
            password: The password to authenticate with.

        Returns:
            A Ranger API client.
        """
        return RangerAPIClient(f"{LOCALHOST_URL}:{APPLICATION_PORT}", (username, password))

    def _probe_credentials(self, function) -> ApiProbe:
        """Authenticate the charm's credentials against Ranger, at most once per hook.

        Args:
            function: The selected charm function.

        Returns:
            The configured credential probe outcome.
        """
        if self._probe_result is None:
            self._probe_result = self._run_credential_probe(function)
        return self._probe_result

    def _recent_rejection(self) -> bool:
        """Report whether Ranger rejected the credentials within the backoff window.

        Returns:
            Whether a rejection is recent enough to reuse.
        """
        relation = self.model.get_relation("peer")
        if relation is None:
            return False
        rejected_at = relation.data[self.unit].get(self.PROBE_REJECTED_AT)
        if not rejected_at:
            return False
        return time.time() - float(rejected_at) < self.PROBE_BACKOFF

    def _record_probe(self, probe: ApiProbe) -> None:
        """Remember when Ranger last rejected the charm's credentials.

        Args:
            probe: The outcome of the latest probe.
        """
        relation = self.model.get_relation("peer")
        if relation is None:
            return
        if probe is ApiProbe.REJECTED:
            relation.data[self.unit][self.PROBE_REJECTED_AT] = str(time.time())
        elif probe is ApiProbe.OK:
            relation.data[self.unit].pop(self.PROBE_REJECTED_AT, None)

    def _clear_probe_backoff(self) -> None:
        """Let the next hook probe Ranger again after the charm changed a password."""
        self._probe_result = None
        relation = self.model.get_relation("peer")
        if relation is not None:
            relation.data[self.unit].pop(self.PROBE_REJECTED_AT, None)

    def _run_credential_probe(self, function) -> ApiProbe:
        """Authenticate the charm's credentials against Ranger.

        Args:
            function: The selected charm function.

        Returns:
            The configured credential probe outcome.

        Raises:
            SecretValidationError: If the usersync-credentials secret is unavailable or invalid.
        """
        if self._recent_rejection():
            return ApiProbe.REJECTED
        if function == "usersync":
            usersync_credentials = self.usersync_credentials
            if usersync_credentials is None:
                return ApiProbe.UNREACHABLE
            url = self.config["policy-mgr-url"]
            username = USERSYNC_USER
            password = usersync_credentials.rangerusersync
        else:
            url = f"{LOCALHOST_URL}:{APPLICATION_PORT}"
            username = ADMIN_USER
            password = self.credentials.get(ADMIN_USER)
            if not password:
                return ApiProbe.UNREACHABLE
        try:
            RangerAPIClient(url, (username, password)).authenticate(self.API_PROBE_TIMEOUT)
        except RangerAuthenticationError:
            self._record_probe(ApiProbe.REJECTED)
            return ApiProbe.REJECTED
        except RangerAPIError:
            logger.info("Ranger API is unavailable; authentication probe will retry next hook.")
            return ApiProbe.UNREACHABLE
        self._record_probe(ApiProbe.OK)
        return ApiProbe.OK

    def _reconcile_api(self, function):
        """Reconcile resources inside the running Ranger server.

        Args:
            function: The selected charm function.
        """
        if self._probe_credentials(function) is not ApiProbe.OK:
            return
        if function != "admin" or not self.unit.is_leader():
            return
        client = self._ranger_api_client()
        try:
            self.provider.reconcile_services(client)
            self.trino_catalog_handler.reconcile_catalogs(client)
        except RangerAPIError:
            logger.warning(
                "Ranger API reconciliation failed; retrying on the next hook", exc_info=True
            )

    def _pebble_layer(self, function, command, context):
        """Build the Pebble layer for the selected Ranger function.

        Args:
            function: The selected charm function.
            command: The workload command.
            context: Environment variables for the workload.

        Returns:
            The Pebble layer definition.
        """
        layer = {
            "summary": f"ranger {function} layer",
            "services": {
                self.name: {
                    "summary": f"ranger {function}",
                    "command": command,
                    "startup": "enabled",
                    "override": "replace",
                    "environment": context,
                }
            },
        }
        if function == "admin":
            layer["checks"] = {
                "up": {
                    "override": "replace",
                    "period": "10s",
                    "http": {"url": "http://localhost:6080/"},
                }
            }
        return layer

    def _reconcile(self):
        """Converge Ranger to the desired state read from the model.

        Guards return early without deferring; convergence resumes on the next
        hook and terminal status is reported by collect-unit-status.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            return
        try:
            cfg = self.config
            _ = self.usersync_credentials
            _ = self.ldap_credentials
        except (ValidationError, SecretValidationError):
            return
        function = cfg["charm-function"].value
        logger.info("reconciling ranger %s", function)
        try:
            self._validate_relations(function)
        except ValueError:
            return

        if function == "admin":
            truststore_pwd = self._ensure_truststore_password()
            if truststore_pwd is None:
                return
            credentials = self.credentials.ensure()
            if credentials is None:
                return
            command, context = self._reconcile_admin(container, truststore_pwd, credentials)
            self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")
        else:
            self.model.unit.close_port(port=APPLICATION_PORT, protocol="tcp")
            command, context = self._reconcile_usersync(container)
        container.add_layer(
            self.name, self._pebble_layer(function, command, context), combine=True
        )
        container.replan()

        self.provider.publish_policy_manager_url()
        if function == "usersync":
            self.ldap.publish_bind_user()
        self._reconcile_api(function)

    @staticmethod
    def _format_validation_error(error: ValidationError) -> str:
        """Format a validation error for Juju status output.

        Args:
            error: The Pydantic validation error to format.

        Returns:
            A concise, actionable configuration error message.
        """
        messages = []
        for validation_error in error.errors():
            location = validation_error["loc"]
            message = validation_error["msg"]
            if location == ("__root__",):
                messages.append(message)
                continue
            option = ".".join(str(part) for part in location).replace("_", "-")
            messages.append(f"{option}: {message}")
        return f"Invalid configuration: {'; '.join(messages)}"


if __name__ == "__main__":  # pragma: nocover
    ops.main(RangerK8SCharm)
