# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""


class RelationNotReady(ValueError):  # noqa: N818
    """A required relation exists but has not yet published usable data."""
