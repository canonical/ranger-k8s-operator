# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines trino-catalog relation event handling methods."""

import logging

from ops import framework

from literals import TRINO_SERVICE_TYPE
from ranger_client import RangerAPIClient, RangerAPIError
from reconcile import TrinoCatalogReconciler

logger = logging.getLogger(__name__)


class TrinoCatalogRelationHandler(framework.Object):
    """Client for trino-catalog relations.

    Event observation is centralized in the charm; this object exposes logic methods
    invoked by the charm reconciler.
    """

    def __init__(self, charm, relation_name="trino-catalog"):
        """Construct.

        Args:
            charm: The charm to attach the handler to.
            relation_name: The name of the relation defaults to trino-catalog.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def reconcile_catalogs(self, client: RangerAPIClient) -> None:
        """Run Trino catalog reconciliation against the Ranger REST API.

        Args:
            client: The configured Ranger API client.

        Returns:
            None.
        """
        info = self.charm.trino_catalog_requirer.get_trino_info()
        catalogs = [catalog.to_dict() for catalog in info["trino_catalogs"]] if info else []
        has_relation = bool(self.charm.model.relations.get(self.relation_name))

        if not has_relation and not catalogs:
            return

        try:
            services = client.list_services_by_type(TRINO_SERVICE_TYPE)
        except RangerAPIError:
            logger.warning(
                "failed to connect to Ranger API, reconciliation will retry on next update-status",
                exc_info=True,
            )
            return

        if not services:
            return

        service_name = services[0].name

        try:
            reconciler = TrinoCatalogReconciler(client, service_name)
            reconciler.reconcile(
                catalogs,
                strict=self.charm.config["enforce-strict-reconciliation"],
            )
        except RangerAPIError:
            logger.warning(
                "reconciliation failed, will retry on next update-status",
                exc_info=True,
            )

    def has_trino_service(self, client: RangerAPIClient):
        """Report whether Ranger has the Trino service backing this relation.

        Args:
            client: The configured Ranger API client.

        Returns:
            None when there is no trino-catalog relation or the API call failed,
            otherwise whether a Trino service exists.
        """
        if not self.charm.model.relations.get(self.relation_name):
            return None
        try:
            services = client.list_services_by_type(TRINO_SERVICE_TYPE)
        except RangerAPIError:
            return None
        return bool(services)
