#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service."""

import logging
import subprocess  # nosec B404
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

from exceptions import RelationNotReady
from literals import (
    ADMIN_ENTRYPOINT,
    ADMIN_USER,
    APPLICATION_PORT,
    LDAP_BIND_CREDENTIAL_CONFIG_KEYS,
    LDAP_TOPOLOGY_CONFIG_KEYS,
    LOCALHOST_URL,
    LOG_FILES,
    METRICS_PORT,
    SUPPRESS_DEBUG_LOGS,
    TRUSTSTORE_SECRET_LABEL,
    USERSYNC_CONFIG_MAPPING,
    USERSYNC_ENTRYPOINT,
)
from ranger_client import RangerAPIClient, RangerAPIError, RangerAuthenticationError
from relations.ldap import LDAPRelationHandler
from relations.opensearch import OpensearchRelationHandler
from relations.postgres import PostgresRelationHandler
from relations.provider import RangerProvider
from relations.trino import TrinoCatalogRelationHandler
from secret_models import LdapCredentials, SecretValidationError, SystemUserPasswords
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

    def __init__(self, *args):
        """Construct.

        Args:
            args: Ignore.
        """
        super().__init__(*args)
        self._system_user_passwords: Optional[SystemUserPasswords] = None
        self._ldap_credentials: Optional[LdapCredentials] = None
        self._configure_logging()
        self.name = "ranger"

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
    def system_user_passwords(self) -> SystemUserPasswords:
        """Resolve the system-users secret once for the current hook.

        Returns:
            The validated system-user passwords.

        Raises:
            SecretValidationError: If the configured secret is unavailable or invalid.
        """
        if self._system_user_passwords is None:
            self._system_user_passwords = self._resolve_secret(
                "system-users", self.config["system-users"], SystemUserPasswords
            )
        return self._system_user_passwords

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
                "Invalid configuration: system-users: secret 'system-users' is missing "
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
            _ = self.system_user_passwords
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

        probe = self._probe_credentials(function)
        if probe is ApiProbe.REJECTED:
            username = "rangerusersync" if function == "usersync" else ADMIN_USER
            event.add_status(
                BlockedStatus(
                    f"Ranger authentication failed for {username}. Revert the system-users "
                    "secret or change the password in the Ranger UI."
                )
            )
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

    def _reconcile_admin(self, container, truststore_pwd):
        """Prepare Ranger Admin configuration and truststore state.

        Args:
            container: The workload container.
            truststore_pwd: The Java truststore password.

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
            "RANGER_ADMIN_PWD": self.system_user_passwords.admin,
            "JAVA_OPTS": (
                f"-Duser.timezone=UTC0 -Djavax.net.ssl.trustStorePassword={truststore_pwd}"
            ),
            "RANGER_USERSYNC_PWD": self.system_user_passwords.rangerusersync,
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
                "RANGER_USERSYNC_PWD": self.system_user_passwords.rangerusersync,
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
            A Ranger API client using the configured administrator credentials.
        """
        return RangerAPIClient(
            f"{LOCALHOST_URL}:{APPLICATION_PORT}",
            (ADMIN_USER, self.system_user_passwords.admin),
        )

    def _probe_credentials(self, function) -> ApiProbe:
        """Authenticate the configured system-user credentials against Ranger.

        Args:
            function: The selected charm function.

        Returns:
            The configured credential probe outcome.

        Raises:
            SecretValidationError: If the system-users secret is unavailable or invalid.
        """
        if function == "usersync":
            url = self.config["policy-mgr-url"]
            username = "rangerusersync"
            password = self.system_user_passwords.rangerusersync
        else:
            url = f"{LOCALHOST_URL}:{APPLICATION_PORT}"
            username = ADMIN_USER
            password = self.system_user_passwords.admin
        try:
            RangerAPIClient(url, (username, password)).authenticate(self.API_PROBE_TIMEOUT)
        except RangerAuthenticationError:
            return ApiProbe.REJECTED
        except RangerAPIError:
            logger.info("Ranger API is unavailable; authentication probe will retry next hook.")
            return ApiProbe.UNREACHABLE
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
            _ = self.system_user_passwords
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
            command, context = self._reconcile_admin(container, truststore_pwd)
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
