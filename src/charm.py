#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service."""

import logging

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import CheckStatus

from groups import RangerGroupManager
from literals import APPLICATION_PORT
from relations.postgres import PostgresRelationHandler
from relations.provider import RangerProvider
from state import State
from utils import log_event_handler, render

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class RangerK8SCharm(ops.CharmBase):
    """Charm the service.

    Attributes:
        external_hostname: DNS listing used for external connections.
    """

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

        self.postgres_relation = DatabaseRequires(
            self,
            relation_name="database",
            database_name=PostgresRelationHandler.DB_NAME,
            extra_user_roles="admin",
        )
        self.postgres_relation_handler = PostgresRelationHandler(self)
        self.provider = RangerProvider(self)
        self.group_manager = RangerGroupManager(self)

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
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event triggered at intervals
        """
        if not self._state.is_ready():
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

    def validate(self):
        """Validate that configuration and relations are valid and ready.

        Raises:
            ValueError: in case of invalid configuration.
        """
        if not self._state.is_ready():
            raise ValueError("peer relation not ready")

        if self.config["application-name"] == "":
            raise ValueError("invalid configuration of application-name")

        if self.config.get("user-group-configuration"):
            self.group_manager._validate()

        self.postgres_relation_handler.validate()

    def update(self, event):
        """Update the Ranger server configuration and re-plan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        logger.info(f"Handling {type(event)} event")
        try:
            self.validate()
        except ValueError as err:
            self.unit.status = BlockedStatus(str(err))
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")

        if self.config.get(
            "user-group-configuration"
        ) and self.unit.status == ActiveStatus("Status check: UP"):
            self.group_manager._handle_synchronize_file(event)

        logger.info("configuring ranger")
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

        config = render("config.jinja", context)
        container.push(
            "/usr/lib/ranger/install.properties", config, make_dirs=True
        )

        logger.info("planning ranger execution")
        pebble_layer = {
            "summary": "ranger server layer",
            "services": {
                self.name: {
                    "summary": "ranger server",
                    "command": "/tmp/entrypoint.sh",  # nosec
                    "startup": "enabled",
                    "override": "replace",
                    "environment": context,
                }
            },
        }
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
