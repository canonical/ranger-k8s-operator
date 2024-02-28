#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service."""

import logging

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import CheckStatus

from literals import (
    APP_NAME,
    APPLICATION_PORT,
    ADMIN_ENTRYPOINT,
    USERSYNC_ENTRYPOINT,
)
from relations.postgres import PostgresRelationHandler
from relations.provider import RangerProvider
from state import State
from utils import log_event_handler, render
from structured_config import CharmConfig

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class RangerK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attributes:
        external_hostname: DNS listing used for external connections.
        config_type: the charm structured config
    """

    config_type = CharmConfig

    @property
    def external_hostname(self):
        """Return the DNS listing used for external connections."""
        return self.config["external-hostname"] or self.app.name

    def __init__(self, *args):
        """Construct.

        Args:
            args: Ignore.
        """
        super().__init__(*args)
        self._state = State(self.app, lambda: self.model.get_relation("peer"))
        self.name = "ranger"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.on.ranger_pebble_ready, self._on_ranger_pebble_ready
        )
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(
            self.on.peer_relation_changed, self._on_peer_relation_changed
        )

        self.postgres_relation = DatabaseRequires(
            self,
            relation_name="database",
            database_name=PostgresRelationHandler.DB_NAME,
            extra_user_roles="admin",
        )
        self.postgres_relation_handler = PostgresRelationHandler(self)
        self.provider = RangerProvider(self)

        # Handle Ingress
        self._require_nginx_route()

    def _require_nginx_route(self):
        """Require nginx-route relation based on current configuration."""
        require_nginx_route(
            charm=self,
            service_hostname=self.external_hostname,
            service_name=self.app.name,
            service_port=APPLICATION_PORT,
            tls_secret_name=self.config["tls-secret-name"],
            backend_protocol="HTTP",
        )

    @log_event_handler(logger)
    def _on_install(self, event):
        """Install application.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = MaintenanceStatus("installing Ranger")

    @log_event_handler(logger)
    def _on_ranger_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Define and start ranger using the Pebble API.

        Args:
            event: The event triggered when the relation changed.
        """
        self.update(event)

    @log_event_handler(logger)
    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle configuration changes.

        Args:
            event: The event triggered when the relation changed.
        """
        self.update(event)

    @log_event_handler(logger)
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
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event triggered at intervals
        """
        if not self._state.is_ready():
            return

        charm_function = self.config["charm-function"].value
        if charm_function == "usersync":
            self.unit.status = ActiveStatus("Status check: UP")
            return

        if not self._state.database_connection:
            return

        container = self.unit.get_container(self.name)

        check = container.get_check("up")
        if check.status != CheckStatus.UP:
            self.unit.status = MaintenanceStatus("Status check: DOWN")
            return

        self.unit.status = ActiveStatus("Status check: UP")

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

    def _configure_ranger_admin(self, container):
        """Prepare Ranger Admin install.properties file.

        Args:
            container: The application container.

        Returns:
            ADMIN_ENTRYPOINT: Entrypoint path for Ranger Admin startup.
            context: Environement variables for pebble plan.
        """
        db_conn = self._state.database_connection
        context = {
            "DB_NAME": db_conn["dbname"],
            "DB_HOST": db_conn["host"],
            "DB_PORT": db_conn["port"],
            "DB_USER": db_conn["user"],
            "DB_PWD": db_conn["password"],
            "RANGER_ADMIN_PWD": self.config["ranger-admin-password"],
            "JAVA_OPTS": "-Duser.timezone=UTC0",
        }
        config = render("admin-config.jinja", context)
        container.push(
            "/usr/lib/ranger/admin/install.properties", config, make_dirs=True
        )
        return ADMIN_ENTRYPOINT, context

    def _configure_ranger_usersync(self, container):
        """Prepare Ranger Usersync install.properties file.

        Args:
            container: The application container.

        Returns:
            USERSYNC_ENTRYPOINT: Entrypoint path for Ranger Usersync startup.
            context: Environement variables for pebble plan.
        """
        context = {}
        for key, value in vars(self.config).items():
            if key.startswith("sync"):
                updated_key = key.upper().replace("-", "_")
                context[updated_key] = value

        context.update(
            {
                "POLICY_MGR_URL": self.config["policy-mgr-url"],
                "RANGER_USERSYNC_PASSWORD": self.config[
                    "ranger-usersync-password"
                ],
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
        if not self._state.is_ready():
            raise ValueError("peer relation not ready")

        if self.config["application-name"] == "":
            raise ValueError("invalid configuration of application-name")

        if self.config["charm-function"].value == "admin":
            self.postgres_relation_handler.validate()

    def update(self, event):
        """Update the Ranger server configuration and re-plan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        try:
            self.validate()
        except ValueError as err:
            self.unit.status = BlockedStatus(str(err))
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        charm_function = self.config["charm-function"].value
        logger.info(f"configuring ranger {charm_function}")

        if charm_function == "admin":
            self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")
            command, context = self._configure_ranger_admin(container)

        if charm_function == "usersync":
            command, context = self._configure_ranger_usersync(container)

        logger.info(f"planning ranger {charm_function} execution")
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


if __name__ == "__main__":  # pragma: nocover
    ops.main(RangerK8SCharm)
