# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Direct recovery writes against the Ranger database."""

import hashlib
import logging
import tempfile

import psycopg2

from exceptions import RangerDatabaseError

logger = logging.getLogger(__name__)

# Clearing old_passwords stops Ranger's password-history check from later rejecting a
# password the charm has already recorded as applied.
RESET_SQL = """\
UPDATE x_portal_user
   SET password = %(password)s,
       old_passwords = NULL,
       password_updated_time = now(),
       update_time = now()
 WHERE login_id = %(login_id)s AND user_src = 0 AND status = 1
"""


def encode_password(password: str, login_id: str) -> str:
    """Reproduce Ranger's password digest, which salts with the login id.

    Args:
        password: The plaintext password.
        login_id: The login name the password belongs to.

    Returns:
        The hexadecimal digest Ranger stores.
    """
    return hashlib.sha256(f"{password}{{{login_id}}}".encode()).hexdigest()


def _connect(connection: dict):
    """Connect to the Ranger database, verifying the server when TLS is advertised.

    Args:
        connection: PostgreSQL connection values from the database relation.

    Returns:
        An open psycopg2 connection.
    """
    params = {key: connection[key] for key in ("dbname", "host", "port", "user", "password")}
    if not connection["tls_ca"]:
        return psycopg2.connect(**params)
    with tempfile.NamedTemporaryFile("w", suffix=".crt") as ca_file:
        ca_file.write(connection["tls_ca"])
        ca_file.flush()
        return psycopg2.connect(**params, sslmode="verify-full", sslrootcert=ca_file.name)


def force_reset(connection: dict, login_id: str, password: str) -> None:
    """Write a password digest straight into the Ranger user table.

    Args:
        connection: PostgreSQL connection values from the database relation.
        login_id: The Ranger internal user to reset.
        password: The password Ranger should accept.

    Raises:
        RangerDatabaseError: If the database is unreachable or the user row is not unique.
    """
    try:
        conn = _connect(connection)
    except psycopg2.Error as err:
        raise RangerDatabaseError(f"Could not connect to the Ranger database: {err}") from err
    try:
        with conn, conn.cursor() as cursor:
            cursor.execute(
                RESET_SQL,
                {"password": encode_password(password, login_id), "login_id": login_id},
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise RangerDatabaseError(
                    f"Expected one enabled internal user named {login_id!r}, "
                    f"found {cursor.rowcount}."
                )
    except psycopg2.Error as err:
        raise RangerDatabaseError(f"Could not reset the password for {login_id!r}: {err}") from err
    finally:
        conn.close()
    logger.info("force reset the password for %s", login_id)
