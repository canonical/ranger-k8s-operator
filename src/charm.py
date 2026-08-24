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
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
    OpenSearchRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from charms.trino_k8s.v0.trino_catalog import TrinoCatalogRequirer
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import CheckStatus, ExecError
from pydantic import ValidationError

from literals import (
    ADMIN_ENTRYPOINT,
    ADMIN_USER,
    APP_NAME,
    APPLICATION_PORT,
    LDAP_BIND_CREDENTIAL_CONFIG_KEYS,
    LDAP_TOPOLOGY_CONFIG_KEYS,
    LOCALHOST_URL,
    LOG_FILES,
    METRICS_PORT,
    SUPPRESS_DEBUG_LOGS,
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
from state import State
from structured_config import CharmConfig
from utils import generate_password, log_event_handler, render, validation_error_handler

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class RangerK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attributes:
        config_type: the charm structured config
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
        self._state = State(self.app, lambda: self.model.get_relation("peer"))
        self.name = "ranger"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.ranger_pebble_ready, self._on_ranger_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(self.on.peer_relation_changed, self._on_peer_relation_changed)

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

        # Trino Catalog
        self.trino_catalog_requirer = TrinoCatalogRequirer(self)
        self.trino_catalog_handler = TrinoCatalogRelationHandler(self)

        # Handle Ingress
        self.ingress = IngressPerAppRequirer(
            self,
            relation_name="ingress",
            port=APPLICATION_PORT,
            strip_prefix=True,
            redirect_https=True,
            scheme="http",
        )
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)
        self.framework.observe(self.ingress.on.revoked, self._on_ingress_revoked)

        # Prometheus
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

        # Loki
        self.log_proxy = LogProxyConsumer(self, log_files=LOG_FILES, relation_name="log-proxy")

        # Grafana
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

    def _clear_secret_cache(self) -> None:
        """Discard resolved secret models before reconciliation."""
        self._system_user_passwords = None
        self._ldap_credentials = None

    def resolve_policy_manager_url(self) -> Optional[str]:
        """Resolve the policy manager URL for the current charm function.

        For admin: explicit config override > live ingress URL > cluster DNS.
        For usersync: config-only; returns None if unset.

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

    @log_event_handler(logger)
    def _on_install(self, event):
        """Install application.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = MaintenanceStatus("installing Ranger")

    @log_event_handler(logger)
    @validation_error_handler
    def _on_ranger_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Define and start ranger using the Pebble API.

        Args:
            event: The event triggered when the relation changed.
        """
        self.update(event)

    @log_event_handler(logger)
    @validation_error_handler
    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle configuration changes.

        Args:
            event: The event triggered when the relation changed.
        """
        self.update(event)

    @log_event_handler(logger)
    @validation_error_handler
    def _on_secret_changed(self, event: ops.SecretChangedEvent):
        """Reconcile after secret content changes.

        Args:
            event: The secret changed event.
        """
        self.update(event)

    @log_event_handler(logger)
    @validation_error_handler
    def _on_ingress_ready(self, event):
        """Handle ingress URL becoming available.

        Args:
            event: The ingress ready event.
        """
        self.provider.reconcile_policy_manager_url()

    @log_event_handler(logger)
    @validation_error_handler
    def _on_ingress_revoked(self, event):
        """Handle ingress URL being revoked.

        Args:
            event: The ingress revoked event.
        """
        self.provider.reconcile_policy_manager_url()

    @log_event_handler(logger)
    @validation_error_handler
    def _on_peer_relation_changed(self, event):
        """Handle peer relation changes.

        Args:
            event: The event triggered when the peer relation changed.
        """
        if self.unit.is_leader():
            return

        self.unit.status = WaitingStatus(f"configuring {APP_NAME}")
        self.update(event)

    @log_event_handler(logger)
    @validation_error_handler
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event triggered at intervals
        """
        if not self._state.is_ready():
            return

        credentials_rejected = self._probe_configured_credentials()
        if credentials_rejected:
            return
        if self.config["charm-function"].value == "usersync":
            self.unit.status = ActiveStatus("Status check: UP")
            return

        if not self._state.database_connection:
            return

        container = self.unit.get_container(self.name)

        if not container.can_connect():
            return

        check = container.get_check("up")
        if check.status != CheckStatus.UP:
            self.unit.status = MaintenanceStatus("Status check: DOWN")
            return

        self.unit.status = ActiveStatus("Status check: UP")

        if self.unit.is_leader():
            self.trino_catalog_handler.run_reconciliation()

    def _on_restart(self, event):
        """Restart application, action handler.

        Args:
            event:The event triggered by the restart action
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        self.unit.status = MaintenanceStatus("restarting ranger")
        container.restart(self.name)
        event.set_results({"result": "ranger successfully restarted"})
        self.unit.status = ActiveStatus()

    def set_truststore_password(self, container):
        """Update the truststore password to the randomly generated one.

        Args:
            container: The application container.
        """
        out, _ = container.exec(["/bin/sh", "-c", "echo $JAVA_HOME"]).wait_output()
        java_home = out.strip()

        command = [
            "keytool",
            "-storepass",
            "changeit",
            "-storepasswd",
            "-new",
            self._state.truststore_pwd,
            "-keystore",
            f"{java_home}/lib/security/cacerts",
        ]
        try:
            container.exec(command).wait_output()
        except (subprocess.CalledProcessError, ExecError) as e:
            if e.stderr and "password was incorrect" in e.stderr:
                return
            if e.stderr and "Warning" in e.stderr:
                return
            logger.debug("Unable to update truststore password %s", e.stderr)

    def _configure_ranger_admin(self, container):
        """Prepare Ranger Admin install.properties file.

        Args:
            container: The application container.

        Returns:
            ADMIN_ENTRYPOINT: Entrypoint path for Ranger Admin startup.
            context: Environment variables for pebble plan.
        """
        db_conn = self._state.database_connection
        if self.unit.is_leader():
            self._state.truststore_pwd = self._state.truststore_pwd or generate_password()
        self.set_truststore_password(container)
        opensearch = self._state.opensearch or {}
        if opensearch.get("is_enabled") and not container.exists("/opensearch.crt"):
            self.opensearch_relation_handler.update_certificates()

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
            "RANGER_ADMIN_PWD": self.system_user_passwords.admin,
            "JAVA_OPTS": (
                f"-Duser.timezone=UTC0"
                f" -Djavax.net.ssl.trustStorePassword={self._state.truststore_pwd}"
            ),
            "RANGER_USERSYNC_PWD": self.system_user_passwords.rangerusersync,
        }
        config = render("admin-config.jinja", context)
        container.push("/usr/lib/ranger/admin/install.properties", config, make_dirs=True)
        return ADMIN_ENTRYPOINT, context

    @staticmethod
    def _render_config_value(value):
        """Render a configuration value for install.properties and the Pebble layer.

        Pebble layers are serialised to YAML, which has no representer for enum
        members, so enums are reduced to their underlying value.

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

    def _configure_ranger_usersync(self, container):
        """Prepare Ranger Usersync install.properties file.

        Args:
            container: The application container.

        Returns:
            USERSYNC_ENTRYPOINT: Entrypoint path for Ranger Usersync startup.
            context: Environment variables for pebble plan.
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
        container.push(
            "/usr/lib/ranger/usersync/install.properties",
            config,
            make_dirs=True,
        )
        return USERSYNC_ENTRYPOINT, context

    def validate(self):
        """Validate that configuration and relations are valid and ready.

        Raises:
            ValueError: in case of invalid configuration.
        """
        config = self.config
        _ = self.system_user_passwords
        if config["ldap-credentials"]:
            _ = self.ldap_credentials

        if not self._state.is_ready():
            raise ValueError("peer relation not ready")

        charm_function = config["charm-function"].value
        if charm_function == "admin":
            self.postgres_relation_handler.validate()

        if charm_function == "usersync":
            self.ldap.validate()

        if self._state.opensearch and charm_function != "admin":
            raise ValueError("Only Ranger admin can relate to OpenSearch.")

    def _probe_configured_credentials(self) -> bool:
        """Authenticate configured system-user credentials against the Ranger API.

        Returns:
            Whether Ranger rejected the configured credentials.

        Raises:
            SecretValidationError: If the system-users secret is unavailable or invalid.
        """
        charm_function = self.config["charm-function"].value
        if charm_function == "usersync":
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
            self.unit.status = BlockedStatus(
                f"Ranger authentication failed for {username}. Revert the system-users secret "
                "and change the password in the Ranger UI."
            )
            return True
        except RangerAPIError:
            logger.info(
                "Ranger API is unavailable; authentication probe will retry on the next hook."
            )
        return False

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

    def update(self, event):
        """Update the Ranger server configuration and re-plan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        self._clear_secret_cache()
        try:
            self.validate()
        except (SecretValidationError, ValidationError, ValueError) as err:
            self._block_on_validation_error(err)
            return

        if self._probe_configured_credentials():
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        charm_function = self.config["charm-function"].value
        logger.info("configuring ranger %s", charm_function)

        self.model.unit.close_port(port=APPLICATION_PORT, protocol="tcp")

        if charm_function == "usersync":
            command, context = self._configure_ranger_usersync(container)
        elif charm_function == "admin":
            self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")
            command, context = self._configure_ranger_admin(container)
        else:
            raise ValueError(
                f"Unhandled charm-function {charm_function!r}; "
                "update this method to support the new function type."
            )

        logger.info("planning ranger %s execution", charm_function)
        pebble_layer = {
            "summary": f"ranger {charm_function} layer",
            "services": {
                self.name: {
                    "summary": f"ranger {charm_function}",
                    "command": command,
                    "startup": "enabled",
                    "override": "replace",
                    "environment": context,
                }
            },
        }
        if charm_function == "admin":
            pebble_layer.update(
                {
                    "checks": {
                        "up": {
                            "override": "replace",
                            "period": "10s",
                            "http": {"url": "http://localhost:6080/"},
                        }
                    }
                },
            )
        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()

        self.unit.status = MaintenanceStatus("replanning application")

    def _block_on_validation_error(self, error):
        """Set a blocked status for a configuration or secret validation error.

        Args:
            error: Validation error raised while resolving configuration or secrets.
        """
        message = (
            self._format_validation_error(error)
            if isinstance(error, ValidationError)
            else str(error)
        )
        self.unit.status = BlockedStatus(message)


if __name__ == "__main__":  # pragma: nocover
    ops.main(RangerK8SCharm)
