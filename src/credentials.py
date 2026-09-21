# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-owned storage for the Ranger internal-user passwords."""

import logging
from typing import Dict, Optional

from ops.model import SecretNotFoundError

from literals import CREDENTIALS_SECRET_LABEL, MANAGED_USERS
from utils import generate_password

logger = logging.getLogger(__name__)


class CredentialStore:
    """Own the application secret holding the Ranger internal-user passwords."""

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm owning the credentials.
        """
        self._charm = charm

    def ensure(self) -> Optional[Dict[str, str]]:
        """Return the stored credentials, generating any the leader has not recorded yet.

        Returns:
            The stored credentials, or None when a non-leader unit cannot yet read a
            complete leader-created secret.
        """
        stored = self.read()
        if all(username in stored for username in MANAGED_USERS):
            return stored
        if not self._charm.unit.is_leader():
            return None
        content = {username: generate_password() for username in MANAGED_USERS}
        content.update(stored)
        self._write(content)
        return content

    def _write(self, content: Dict[str, str]) -> None:
        """Create the credentials secret, or replace its content when it exists.

        Args:
            content: The credentials to store.
        """
        try:
            secret = self._charm.model.get_secret(label=CREDENTIALS_SECRET_LABEL)
        except SecretNotFoundError:
            self._charm.app.add_secret(content, label=CREDENTIALS_SECRET_LABEL)
            return
        secret.set_content(content)

    def read(self) -> Dict[str, str]:
        """Read the stored credentials.

        Returns:
            The stored credentials, or an empty mapping when the secret does not exist.
        """
        try:
            secret = self._charm.model.get_secret(label=CREDENTIALS_SECRET_LABEL)
        except SecretNotFoundError:
            return {}
        return secret.get_content(refresh=True)

    def get(self, username: str) -> Optional[str]:
        """Read a single stored password.

        Args:
            username: The Ranger internal user to read.

        Returns:
            The stored password, or None when it is not recorded.
        """
        return self.read().get(username)

    def set(self, username: str, password: str) -> None:
        """Record a password applied to Ranger.

        Args:
            username: The Ranger internal user to record.
            password: The password Ranger now holds.
        """
        secret = self._charm.model.get_secret(label=CREDENTIALS_SECRET_LABEL)
        content = dict(secret.get_content(refresh=True))
        content[username] = password
        secret.set_content(content)
