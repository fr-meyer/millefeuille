"""Retry utilities for the Millefeuille.

This module provides a reusable retry decorator with exponential backoff for
handling transient failures in network requests, API calls, and other operations
that may fail temporarily but succeed on retry.
"""

from collections.abc import Callable
from functools import wraps
import logging
import time

logger = logging.getLogger(__name__)


def retry_with_backoff(
    max_attempts: int,
    initial_delay: float,
    backoff_multiplier: float,
    max_delay: float,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> Callable:
    """Decorator that retries a function with exponential backoff on failure.

    Implements exponential backoff retry logic with configurable parameters.
    The delay between retries follows the formula:
    delay = min(initial_delay * (backoff_multiplier ** (attempt - 1)), max_delay)

    Args:
        max_attempts: Maximum number of attempts (including the first attempt).
            Must be at least 1.
        initial_delay: Initial delay in seconds before the first retry.
            Must be greater than 0.
        backoff_multiplier: Multiplier for exponential backoff. Each retry delay
            is multiplied by this factor. Must be greater than 0.
        max_delay: Maximum delay cap in seconds. Prevents delays from growing
            too large. Must be greater than or equal to initial_delay.
        exceptions: Tuple of exception types to catch and retry on. Defaults to
            (Exception,) to catch all exceptions. Only exceptions of these types
            (or their subclasses) will trigger retries.
        on_retry: Optional callback function called before each retry attempt.
            Receives three arguments: attempt number (1-indexed), delay in seconds,
            and the exception that triggered the retry. Useful for logging or
            context-aware retry handling. If the callback raises an exception,
            it will propagate and stop the retry loop.

    Returns:
        Decorator function that wraps the target function with retry logic.

    Raises:
        The last exception raised by the decorated function if all attempts fail.
        Any exception raised by the on_retry callback will propagate immediately.

    Example:
        Basic usage with default exception handling:

        >>> @retry_with_backoff(
        ...     max_attempts=3,
        ...     initial_delay=2.0,
        ...     backoff_multiplier=2.0,
        ...     max_delay=16.0
        ... )
        ... def fetch_data():
        ...     return requests.get("https://api.example.com/data")

        Usage with specific exception types and retry callback:

        >>> def log_retry(attempt: int, delay: float, exception: Exception):
        ...     logger.info(f"Retry attempt {attempt} after {delay}s: {exception}")

        >>> @retry_with_backoff(
        ...     max_attempts=3,
        ...     initial_delay=2.0,
        ...     backoff_multiplier=2.0,
        ...     max_delay=16.0,
        ...     exceptions=(requests.RequestException, URLError),
        ...     on_retry=log_retry
        ... )
        ... def download_file(url: str):
        ...     return requests.get(url)

        Usage with function arguments:

        >>> @retry_with_backoff(
        ...     max_attempts=5,
        ...     initial_delay=1.0,
        ...     backoff_multiplier=1.5,
        ...     max_delay=10.0
        ... )
        ... def process_item(item_id: str, timeout: int = 30):
        ...     return api_client.process(item_id, timeout=timeout)
    """
    # Validate parameters at decorator creation time to fail fast on invalid configs
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    if initial_delay <= 0:
        raise ValueError(f"initial_delay must be greater than 0, got {initial_delay}")
    if backoff_multiplier <= 0:
        raise ValueError(
            f"backoff_multiplier must be greater than 0, got {backoff_multiplier}"
        )
    if max_delay <= 0:
        raise ValueError(f"max_delay must be greater than 0, got {max_delay}")
    if max_delay < initial_delay:
        raise ValueError(
            f"max_delay ({max_delay}) must be greater than or equal to "
            f"initial_delay ({initial_delay})"
        )

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    # Attempt to execute the function
                    return func(*args, **kwargs)
                except exceptions as e:
                    # Store the exception for potential re-raising
                    last_exception = e

                    # If this is the last attempt, re-raise immediately
                    if attempt == max_attempts:
                        raise

                    # Calculate delay using exponential backoff formula:
                    # delay = min(initial_delay * (backoff_multiplier **
                    # (attempt - 1)), max_delay)
                    delay = min(
                        initial_delay * (backoff_multiplier ** (attempt - 1)), max_delay
                    )

                    # Call retry callback if provided
                    if on_retry is not None:
                        try:
                            on_retry(attempt, delay, e)
                        except Exception:
                            # If callback raises, propagate immediately
                            raise

                    # Sleep for calculated delay before retrying
                    time.sleep(delay)

            # This should never be reached, but included for type safety
            if last_exception is not None:
                raise last_exception

        return wrapper

    return decorator
