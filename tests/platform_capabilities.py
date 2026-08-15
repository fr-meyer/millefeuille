"""Shared platform capability markers for filesystem security tests."""

from __future__ import annotations

import ctypes
import os
import sys
import unittest


def _has_atomic_no_replace_rename() -> bool:
    """Return whether the runtime exposes Millefeuille's atomic rename primitive."""

    if os.name != "posix":
        return False
    symbol = "renameatx_np" if sys.platform == "darwin" else "renameat2"
    try:
        library = ctypes.CDLL(None, use_errno=True)
        getattr(library, symbol)
    except (AttributeError, OSError):
        return False
    return True


def supports_secure_nofollow_writes() -> bool:
    """Return whether descriptor-relative, no-follow writes are available."""

    required_dir_fd_functions = (os.open, os.mkdir, os.stat, os.unlink)
    return (
        hasattr(os, "O_NOFOLLOW")
        and all(
            function in os.supports_dir_fd
            for function in required_dir_fd_functions
        )
        and os.stat in os.supports_follow_symlinks
        and hasattr(os, "fchmod")
        and hasattr(os, "fsync")
    )


def supports_posix_batch_publication() -> bool:
    """Return whether atomic retrieval-batch publication can be exercised."""

    if os.name != "posix" or not supports_secure_nofollow_writes():
        return False
    try:
        import fcntl  # noqa: F401
    except (ImportError, OSError):
        return False
    return _has_atomic_no_replace_rename()


SECURE_NOFOLLOW_WRITES = supports_secure_nofollow_writes()
POSIX_BATCH_PUBLICATION = supports_posix_batch_publication()

requires_secure_nofollow_writes = unittest.skipUnless(
    SECURE_NOFOLLOW_WRITES,
    "requires descriptor-relative no-follow filesystem writes",
)
requires_unsupported_secure_nofollow_writes = unittest.skipIf(
    SECURE_NOFOLLOW_WRITES,
    "requires a platform without descriptor-relative no-follow writes",
)
requires_posix_batch_publication = unittest.skipUnless(
    POSIX_BATCH_PUBLICATION,
    "requires POSIX locks and atomic descriptor-relative batch publication",
)
requires_unsupported_posix_batch_publication = unittest.skipIf(
    POSIX_BATCH_PUBLICATION,
    "requires a platform without POSIX atomic batch publication",
)
