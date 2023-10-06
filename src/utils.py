# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define helpers methods."""

import functools
import os
import random
import string
from jinja2 import Environment, FileSystemLoader


def render(template_name, context):
    """Render the template with the given name using the given context dict.

    Args:
        template_name: File name to read the template from.
        context: Dict used for rendering.

    Returns:
        A dict containing the rendered template.
    """
    charm_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir)
    )
    loader = FileSystemLoader(os.path.join(charm_dir, "templates"))
    return (
        Environment(loader=loader, autoescape=True)
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
                event: The event triggered when the relation changes.

            Returns:
                Decorated method.
            """
            logger.info(
                f"* running {self.__class__.__name__}.{method.__name__}"
            )
            try:
                return method(self, event)
            finally:
                logger.info(
                    f"* completed {self.__class__.__name__}.{method.__name__}"
                )

        return decorated

    return decorator


def generate_random_string(length) -> str:
    """Create randomized string for use as app passwords and username ID.

    Args:
        length: number of characters to generate

    Returns:
        String of randomized letter+digit characters
    """
    uppercase_letters = string.ascii_uppercase
    lowercase_letters = string.ascii_lowercase
    digits = string.digits

    all_characters = uppercase_letters + lowercase_letters + digits

    password = (
        random.choice(uppercase_letters)
        + random.choice(lowercase_letters)
        + random.choice(digits)
    )
    password += "".join(
        random.choice(all_characters) for _ in range(length - 3)
    )

    return "".join(random.sample(password, len(password)))
