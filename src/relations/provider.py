# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Ranger client relation hooks & helpers."""


import logging

from ops.charm import RelationBrokenEvent, CharmBase
from ops.framework import Object
from utils import log_event_handler

logger = logging.getLogger(__name__)


class RangerProvider(Object):
    """Defines functionality for the 'provides' side of the 'ranger-client' relation.

    Hook events observed:
        - relation-created
        - relation-broken
    """

    def __init__(self, charm: CharmBase, relation_name: str = "policy") -> None:
        """Constructor for RangerProvider object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name
        
        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_created, self._on_relation_created
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_updated, self._on_relation_created
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken, self._on_relation_broken
        )

        self.charm = charm



