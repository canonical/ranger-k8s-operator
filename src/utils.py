# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define helpers methods."""

import functools
import logging
import os
import secrets
import string
import time

from apache_ranger.exceptions import RangerServiceException
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
    """Create a secure randomized string for use as external user passwords.

    Args:
        length: number of characters to generate

    Returns:
        String of randomized letter+digit characters
    """
    uppercase_letters = string.ascii_uppercase
    lowercase_letters = string.ascii_lowercase
    digits = string.digits

    all_characters = uppercase_letters + lowercase_letters + digits

    characters = [
        secrets.choice(uppercase_letters)
        + secrets.choice(lowercase_letters)
        + secrets.choice(digits)
    ]
    characters.extend(
        secrets.choice(all_characters) for _ in range(length - 3)
    )
    secrets.SystemRandom().shuffle(characters)
    password = "".join(characters)

    return password


def retry(max_retries=3, delay=2, backoff=2):
    """Decorate function to retry executing upon failure.

    Args:
        max_retries: The maximum number of times to retry the decorated function.
        delay: The initial delay (in seconds) before the first retry.
        backoff: The factor by which the delay increases with each retry.

    Returns:
        decorator: A retry decorator function.
    """

    def decorator(func):
        """Apply decorator function to the target function.

        Args:
            func: The function to decorate.

        Returns:
            wrapper: A decorated function that will be retried upon failure.
        """

        def wrapper(*args, **kwargs):
            """Execute wrapper for the decorated function and handle retries.

            Args:
                args: Positional arguments passed to the decorated function.
                kwargs: Keyword arguments passed to the decorated function.

            Returns:
                result: The result of the decorated function if successful.
                None: If max_retries are reached without success, returns None.

            Raises:
                RangerServiceException: If max_retries are reached without success.
            """
            logger = logging.getLogger(__name__)

            current_delay = delay  # Define current_delay before using it
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Request failed (attempt {attempt + 1}): {e}"
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.exception("Max retries reached for request")
                        raise RangerServiceException(
                            "Max retries reached for request."
                        ) from e
            return None

        return wrapper

    return decorator


def handle_service_error(func):
    """Handle RangerServiceException while interacting with the Ranger API.

    Args:
        func: The function to decorate.

    Returns:
        wrapper: A decorated function that raises an error on failure.
    """

    def wrapper(*args, **kwargs):
        """Execute wrapper for the decorated function and handle errors.

        Args:
            args: Positional arguments passed to the decorated function.
            kwargs: Keyword arguments passed to the decorated function.

        Returns:
            result: The result of the decorated function if successful.

        Raises:
            ExecError: In case the command fails to execute successfully.
        """
        logger = logging.getLogger(__name__)

        try:
            result = func(*args, **kwargs)
            return result
        except RangerServiceException:
            logger.exception(f"Failed to execute {func.__name__}:")
            raise

    return wrapper
