# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define helpers methods."""

import functools
import hashlib
import os
import secrets
import string

from jinja2 import Environment, FileSystemLoader, select_autoescape

from secret_models import validate_password

PASSWORD_LENGTH = 32


def render(template_name, context):
    """Render the template with the given name using the given context dict.

    Args:
        template_name: File name to read the template from.
        context: Dict used for rendering.

    Returns:
        A dict containing the rendered template.
    """
    charm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    loader = FileSystemLoader(os.path.join(charm_dir, "templates"))
    return (
        Environment(
            loader=loader,
            autoescape=select_autoescape(
                enabled_extensions=("html", "xml"),
                default_for_string=False,
                default=False,
            ),
        )
        .get_template(template_name)
        .render(**context)
    )


def log_event_handler(logger):
    """Log with the provided logger when a event handler method is executed.

    Args:
        logger: logger used to log events.

    Returns:
        Decorator wrapper.
    """

    def decorator(method):
        """Log decorator wrapper.

        Args:
            method: method wrapped by the decorator.

        Returns:
            Decorated method.
        """

        @functools.wraps(method)
        def decorated(self, event):
            """Log decorator method.

            Args:
                self: The charm instance.
                event: The event triggered when the relation changes.

            Returns:
                Decorated method.
            """
            logger.info(f"* running {self.__class__.__name__}.{method.__name__}")
            try:
                return method(self, event)
            finally:
                logger.info(f"* completed {self.__class__.__name__}.{method.__name__}")

        return decorated

    return decorator


def generate_password():
    """Create a randomized password meeting Ranger's complexity requirements.

    Returns:
        String of 32 randomized letter+digit characters.
    """
    rng = secrets.SystemRandom()
    characters = [
        rng.choice(string.ascii_uppercase),
        rng.choice(string.ascii_lowercase),
        rng.choice(string.digits),
    ]
    alphabet = string.ascii_letters + string.digits
    characters += [rng.choice(alphabet) for _ in range(PASSWORD_LENGTH - len(characters))]
    rng.shuffle(characters)
    # Revalidated so a later change to the password rules cannot silently outgrow this.
    return validate_password("".join(characters))


def content_hash(value: str) -> str:
    """Return the SHA-256 digest of a string.

    Args:
        value: String to hash.

    Returns:
        The hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(value.encode()).hexdigest()
