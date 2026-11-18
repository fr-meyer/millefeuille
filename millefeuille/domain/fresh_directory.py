"""Acquire privately, publish known inode with Linux no-replace rename.

Attacker authority is confined to the final parent, not the caller-designated
protected namespace (default /tmp). Unrestricted same-UID OS attackers are
outside this boundary. See docs/openkb-import.md for the confinement contract.
"""

from contextlib import contextmanager, suppress
import ctypes
import errno
import os
from pathlib import Path
import stat
import tempfile

from millefeuille.domain.metadata_transaction import require_metadata_write_support
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import (
    _open_directory_path_no_follow,
    _open_or_create_directory_path_no_follow,
    _open_relative_directory_fd,
)


def _publish_directory(source_fd, target_fd, name):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise MillefeuilleContractError(
            "atomic no-replace directory publication unavailable"
        )
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(source_fd, b"entry", target_fd, os.fsencode(name), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


@contextmanager
def reserve_directory(path, *, protected_staging_root=None):
    require_metadata_write_support()
    path = Path(os.path.abspath(path))
    # Final-parent-only mutation authority must not include the acquisition root.
    protected = checked_staging_root(protected_staging_root, [path])
    protected_fd = _open_directory_path_no_follow(
        protected, label="protected namespace"
    )
    parent = staging_fd = fd = None
    staging = None
    try:
        info = os.fstat(protected_fd)
        # Caller-designated root is outside final-parent mutation authority.
        staging = Path(tempfile.mkdtemp(prefix="millefeuille-reserve-", dir=protected))
        staging_fd = _open_directory_path_no_follow(
            staging, label="private reservation"
        )
        owned = os.fstat(staging_fd)
        if owned.st_uid != os.geteuid() or stat.S_IMODE(owned.st_mode) != 0o700:
            raise MillefeuilleContractError("private reservation permissions drift")
        os.mkdir("entry", 0o700, dir_fd=staging_fd)
        fd = _open_relative_directory_fd(staging_fd, "entry")
        identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        parent = _open_or_create_directory_path_no_follow(
            path.parent, label="destination parent"
        )
        parent_identity = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        if parent_identity in (
            (info.st_dev, info.st_ino),
            (owned.st_dev, owned.st_ino),
        ):
            raise MillefeuilleContractError("destination overlaps protected namespace")
        _publish_directory(staging_fd, parent, path.name)
        rebound = _open_directory_path_no_follow(path, label="reserved destination")
        try:
            if identity != (os.fstat(rebound).st_dev, os.fstat(rebound).st_ino):
                raise MillefeuilleContractError("destination ownership drift")
        finally:
            os.close(rebound)
        yield fd, identity
    except OSError as exc:
        detail = (
            "cross-device or unsupported publication"
            if exc.errno in (errno.EXDEV, errno.ENOSYS, errno.EINVAL)
            else "reservation refused"
        )
        raise MillefeuilleContractError(detail) from exc
    finally:
        for descriptor in (fd, parent, staging_fd, protected_fd):
            if descriptor is not None:
                os.close(descriptor)
        # Never recursively remove uncertain state or recovery journals.
        if staging is not None:
            with suppress(OSError):
                staging.rmdir()  # only the empty protected container after publication


def checked_staging_root(root, destinations):
    """Validate isolation before even writing intermediate computation artifacts."""
    protected = Path(os.path.abspath(root or "/tmp"))
    for destination in destinations:
        if protected.is_relative_to(Path(os.path.abspath(destination)).parent):
            raise MillefeuilleContractError("destination overlaps protected staging")
    for ancestor in (protected, *protected.parents):
        fd = _open_directory_path_no_follow(ancestor, label="protected staging")
        try:
            info = os.fstat(fd)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022 and not sticky_root
            ):
                raise MillefeuilleContractError("untrusted staging ancestor")
        finally:
            os.close(fd)
    return protected
