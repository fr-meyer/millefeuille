"""Progress bar utilities for the Millefeuille.

This module provides a ProgressBar class that wraps tqdm for consistent
progress display across the pipeline with unicode/emoji support.
"""

import os
import sys

from tqdm import tqdm


def _supports_unicode() -> bool:
    """Detect if terminal supports unicode/emoji.

    Checks system encoding and environment variables to determine if the
    terminal can display unicode characters and emoji.

    Returns:
        True if terminal supports unicode, False otherwise
    """
    # Check for explicit ASCII-only mode
    if os.environ.get("FORCE_ASCII") == "1":
        return False

    # Check stdout encoding
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding is None:
        return False

    # Common unicode-capable encodings
    unicode_encodings = {"utf-8", "utf-16", "utf-32", "utf-8-sig"}
    return encoding.lower() in unicode_encodings


class ProgressBar:
    """Progress bar wrapper around tqdm for consistent styling.

    Provides a context manager interface for progress tracking with automatic
    cleanup and graceful unicode/emoji handling.

    Args:
        total: Total number of items to process
        desc: Description text to display with the progress bar
        unit: Unit label for items (e.g., "page", "item", "document")

    Example:
        >>> with ProgressBar(total=100, desc="Processing pages", unit="page") as pbar:
        ...     for page in pages:
        ...         # process page
        ...         pbar.update(1)
    """

    def __init__(self, total: int, desc: str, unit: str = "item") -> None:
        """Initialize progress bar.

        Args:
            total: Total number of items to process
            desc: Description text to display
            unit: Unit label for items
        """
        self.total = total
        self.desc = desc
        self.unit = unit
        self._pbar: tqdm | None = None

    def __enter__(self) -> "ProgressBar":
        """Enter context manager and initialize progress bar.

        Returns:
            Self for use in with statements
        """
        # Determine if we should use ASCII mode
        use_ascii = not _supports_unicode()

        # Default bar format
        bar_format = "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"

        self._pbar = tqdm(
            total=self.total,
            desc=self.desc,
            unit=self.unit,
            ncols=80,
            bar_format=bar_format,
            ascii=use_ascii,
        )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager and close progress bar.

        Args:
            exc_type: Exception type if exception occurred
            exc_val: Exception value if exception occurred
            exc_tb: Exception traceback if exception occurred
        """
        self.close()

    def update(self, n: int = 1) -> None:
        """Update progress bar by n items.

        Args:
            n: Number of items to increment progress by (default: 1)
        """
        if self._pbar is not None:
            self._pbar.update(n)

    def set_postfix(self, postfix: dict) -> None:
        """Set postfix text displayed after the progress bar.

        Args:
            postfix: Dictionary of key-value pairs to display as postfix.
                Values will be formatted as "key=value" pairs.
        """
        if self._pbar is not None:
            self._pbar.set_postfix(postfix)

    def close(self) -> None:
        """Manually close and cleanup progress bar."""
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
