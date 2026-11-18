"""Held-descriptor root evidence custody. No provisioning on ordinary reads."""

import hashlib
import json
import os
from pathlib import Path
import stat

from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.paper_card_trusted_approval import (
    _reject_json_constant,
    _unique_json_object,
)


def decode(raw):
    return json.loads(
        raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant
    )


def _identity(s):
    return (
        s.st_dev,
        s.st_ino,
        s.st_size,
        s.st_mtime_ns,
        s.st_ctime_ns,
        s.st_nlink,
        s.st_mode,
        s.st_uid,
    )


def protected_read(path, *, uid=0, limit=1048576):
    """Trusted ancestors and private single-link files; root remains trusted."""
    if os.name != "posix" or not all(
        hasattr(os, name) for name in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY")
    ):
        raise Error("protected evidence reads require POSIX no-follow descriptors")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise Error("absolute protected path required")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open("/", flags | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
            info = os.fstat(directory)
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o022:
                raise Error("untrusted ancestor")
        fd = os.open(path.name, flags, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != uid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size > limit
            ):
                raise Error("untrusted evidence file")
            chunks = bytearray()
            while len(chunks) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            after = os.fstat(fd)
            linked = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (
                len(chunks) > limit
                or _identity(before) != _identity(after)
                or _identity(after) != _identity(linked)
            ):
                raise Error("evidence changed during read")
            return bytes(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def checked_file(root, descriptor):
    if set(descriptor) != {"name", "sha256"}:
        raise Error("exact file commitment required")
    name = descriptor["name"]
    if (
        not isinstance(name, str)
        or not name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
    ):
        raise Error("relative evidence name required")
    raw = protected_read(Path(root) / name)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise Error("evidence commitment mismatch")
    return raw
