"""POSIX durable exclusive publication for final-stage journals."""

import os
import tempfile

from millefeuille.domain.taxonomy_store import read_bytes, require, safe_path


def sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, raw):
    path = safe_path(path)
    if path.exists():
        require(read_bytes(path) == raw)
        return
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path, follow_symlinks=False)
        except FileExistsError:
            require(read_bytes(path) == raw)
        sync(path.parent)
    finally:
        os.unlink(name)
        sync(path.parent)


def reserve(path):
    path = safe_path(path)
    path.mkdir(mode=0o700)
    sync(path)
    sync(path.parent)
    sync(path.parent.parent)
