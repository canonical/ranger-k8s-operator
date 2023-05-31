#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging

import ops
from charms.data_platform_libs.v0.database_requires import DatabaseRequires
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from relations.postgres import PostgresRelationHandler
from state import State
from utils import log_event_handler, render

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class RangerK8SCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.state = State(self.app, lambda: self.model.get_relation("peer"))
        self.name = "ranger"

        self.framework.observe(self.on.ranger_pebble_ready, self._on_ranger_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.postgres_relation = DatabaseRequires(
            self, relation_name="db", database_name=PostgresRelationHandler.DB_NAME
        )
        self.postgres_relation_handler = PostgresRelationHandler(self)

    @log_event_handler(logger)
    def _on_ranger_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Define and start temporal using the Pebble API.

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
        self.unit.status = WaitingStatus("configuring ranger")
        self.update(event)

    def validate(self):
        """Validate that configuration and relations are valid and ready.

        Raises:
            ValueError: in case of invalid configuration.
        """
        log_level = self.model.config["log-level"].lower()
        if log_level not in VALID_LOG_LEVELS:
            raise ValueError(f"config: invalid log level {log_level!r}")
        if not self.state.is_ready():
            raise ValueError("peer relation not ready")

        self.postgres_relation_handler.validate()

    def update(self, event):
        """Update the Temporal server configuration and re-plan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        try:
            self.validate()
        except ValueError as err:
            self.unit.status = BlockedStatus(str(err))
            return

        # if self.unit.is_leader():
        # self._open_service_ports()

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        logger.info("configuring ranger")
        options = {
            "log-level": "LOG_LEVEL",
        }
        context = {config_key: self.config[key] for key, config_key in options.items()}
        db_conn = self.state.database_connections["db"]
        context.update(
            {
                "DB_NAME": db_conn["dbname"],
                "DB_HOST": db_conn["host"],
                "DB_PORT": db_conn["port"],
                "DB_USER": db_conn["user"],
                "DB_PWD": db_conn["password"],
                "RANGER_ADMIN_PWD": self.config["ranger-admin-password"],
                "JAVA_OPTS": "-Duser.timezone=UTC0",
            }
        )

        config = render("config.jinja", context)
        container.push("/usr/lib/ranger/install.properties", config, make_dirs=True)

        logger.info("planning ranger execution")
        pebble_layer = {
            "summary": "ranger server layer",
            "services": {
                self.name: {
                    "summary": "ranger server",
                    "command": "/tmp/entrypoint.sh",
                    # "command": "sleep infinity",
                    "startup": "enabled",
                    "override": "replace",
                    # Including config values here so that a change in the
                    # config forces re-planning to restart the service.
                    "environment": context,
                }
            },
        }
        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()

        # probably run some health check before becoming active
        self.unit.status = ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(RangerK8SCharm)
