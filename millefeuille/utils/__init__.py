"""Utility functions and helpers for the Millefeuille.

This package provides logging, progress tracking, and retry utilities that integrate
with Hydra's configuration system and support unicode/emoji for user-friendly
terminal output.
"""

from .logging import log_error, log_item_start, setup_logging
from .progress import ProgressBar
from .retry import retry_with_backoff

__all__ = [
    "setup_logging",
    "log_item_start",
    "log_error",
    "ProgressBar",
    "retry_with_backoff",
]
