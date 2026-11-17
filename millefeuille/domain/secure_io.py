"""Descriptor-relative, no-follow reads for local Millefeuille artifacts."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar
import errno
import json
import os
from pathlib import Path
import stat
from types import TracebackType
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError

_WINDOWS = os.name == "nt"
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


_StatObjectIdentity = tuple[int, int, int]
_StatStabilitySnapshot = tuple[int, ...]
_StatRecord = tuple[_StatObjectIdentity, _StatStabilitySnapshot]
_READ_CHUNK_BYTES = 65_536
_READ_BUDGET = ContextVar("semantic_read_budget", default=None)


def _debit_metadata(*, opens=0, read_bytes=0):
    """Conservative logical metadata charges before filesystem operations."""
    budget = _READ_BUDGET.get()
    if budget is not None:
        budget.debit(opens=opens, read_bytes=read_bytes)


def _entry_limit(value):
    if type(value) is not int or not 1 <= value <= 513:
        raise MillefeuilleContractError(
            "directory entry_limit must be an integer in 1..513"
        )
    return value


def _validated_max_bytes(max_bytes: int | None, *, label: str) -> int | None:
    if max_bytes is None:
        return None
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise MillefeuilleContractError(
            f"{label} max_bytes must be a non-negative integer"
        )
    return max_bytes


def _read_opened_file_bytes(
    fd: int,
    opened_stat: os.stat_result,
    *,
    path: Path,
    label: str,
    max_bytes: int | None,
) -> bytes:
    """Read one opened file without accumulating beyond ``max_bytes``."""

    if max_bytes is not None and opened_stat.st_size > max_bytes:
        raise MillefeuilleContractError(f"{label} exceeds {max_bytes} bytes")

    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        read_size = _READ_CHUNK_BYTES
        if max_bytes is not None:
            # Probe one byte past the remaining allowance so growth after the
            # opened-file metadata check is rejected without an unbounded read.
            read_size = min(read_size, max_bytes - total_bytes + 1)
        chunk = os.read(fd, read_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if max_bytes is not None and total_bytes > max_bytes:
            raise MillefeuilleContractError(f"{label} exceeds {max_bytes} bytes")
        chunks.append(chunk)

    payload = b"".join(chunks)
    if max_bytes is not None and len(payload) > max_bytes:
        raise MillefeuilleContractError(f"{label} exceeds {max_bytes} bytes")
    if len(payload) != opened_stat.st_size:
        raise MillefeuilleContractError(f"{label} changed while read: {path}")
    return payload


def _supports_no_follow() -> bool:
    return hasattr(os, "O_NOFOLLOW")


def _regular_file_read_flags(*, no_follow: bool) -> int:
    """Return flags for an exact byte read of one regular file."""

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOINHERIT"):
        flags |= os.O_NOINHERIT
    if no_follow and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _open_directory_fd(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _open_relative_directory_fd(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


def _reject_directory_markers(
    directory_fd: int,
    *,
    marker_names: frozenset[str],
    path: Path,
    label: str,
) -> None:
    for marker_name in sorted(marker_names):
        try:
            os.stat(marker_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not inspect {label} boundary {path}: {exc}"
            ) from exc
        raise MillefeuilleContractError(
            f"{label} must not be created beneath run-package marker "
            f"{marker_name!r}: {path}"
        )


def _open_directory_path_no_follow(
    path: Path,
    *,
    label: str,
    forbidden_ancestor_markers: frozenset[str] = frozenset(),
) -> int:
    if not _supports_no_follow():
        raise MillefeuilleContractError(
            f"{label} requires no-follow filesystem reads on this platform"
        )
    target = Path(path)
    anchor = Path(target.anchor) if target.is_absolute() else Path(".")
    parts = target.parts[1:] if target.is_absolute() else target.parts
    current_fd = _open_directory_fd(anchor)
    try:
        _reject_directory_markers(
            current_fd,
            marker_names=forbidden_ancestor_markers,
            path=anchor,
            label=label,
        )
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise MillefeuilleContractError(
                    f"{label} path must not contain parent traversal: {target}"
                )
            next_fd = _open_relative_directory_fd(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
            _reject_directory_markers(
                current_fd,
                marker_names=forbidden_ancestor_markers,
                path=target,
                label=label,
            )
        opened_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(opened_stat.st_mode):
            raise MillefeuilleContractError(f"{label} is not a directory: {target}")
        return current_fd
    except MillefeuilleContractError:
        os.close(current_fd)
        raise
    except FileNotFoundError as exc:
        os.close(current_fd)
        raise MillefeuilleContractError(f"{label} not found: {target}") from exc
    except OSError as exc:
        os.close(current_fd)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or non-directories: "
                f"{target}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open {label} {target}: {exc}"
        ) from exc


def _open_or_create_directory_path_no_follow(
    path: Path,
    *,
    label: str,
    forbidden_ancestor_markers: frozenset[str] = frozenset(),
) -> int:
    """Open one directory path, securely creating missing components."""

    if not _supports_no_follow():
        raise MillefeuilleContractError(
            f"{label} requires no-follow filesystem writes on this platform"
        )
    target = Path(path)
    anchor = Path(target.anchor) if target.is_absolute() else Path(".")
    parts = target.parts[1:] if target.is_absolute() else target.parts
    current_fd = _open_directory_fd(anchor)
    try:
        _reject_directory_markers(
            current_fd,
            marker_names=forbidden_ancestor_markers,
            path=anchor,
            label=label,
        )
        for part in parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise MillefeuilleContractError(
                    f"{label} path must not contain parent traversal: {target}"
                )
            try:
                _debit_metadata(opens=2, read_bytes=1)
                next_fd = _open_relative_directory_fd(current_fd, part)
            except FileNotFoundError:
                # Another writer may win the creation race. The no-follow open
                # below still decides whether the new entry is acceptable.
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                next_fd = _open_relative_directory_fd(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
            _reject_directory_markers(
                current_fd,
                marker_names=forbidden_ancestor_markers,
                path=target,
                label=label,
            )
        opened_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(opened_stat.st_mode):
            raise MillefeuilleContractError(f"{label} is not a directory: {target}")
        return current_fd
    except MillefeuilleContractError:
        os.close(current_fd)
        raise
    except OSError as exc:
        os.close(current_fd)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or non-directories: "
                f"{target}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open or create {label} {target}: {exc}"
        ) from exc


_DIRECTORY_READ_SCOPE = ContextVar("directory_read_scope", default=None)


@contextmanager
def held_directory_reads():
    """Reuse directory handles within one synchronous plan, never file bytes."""
    directories = {}
    token = _DIRECTORY_READ_SCOPE.set(directories)
    try:
        yield
    finally:
        _DIRECTORY_READ_SCOPE.reset(token)
        for fd in directories.values():
            os.close(fd)


def _held_parent_directory_fd(target: Path, *, label: str, directories) -> int:
    key = Path(target.anchor)
    if key not in directories:
        directories[key] = _open_directory_fd(key)
    current = directories[key]
    for part in target.parts[1:-1]:
        key = key / part
        named = os.stat(part, dir_fd=current, follow_symlinks=False)
        if not stat.S_ISDIR(named.st_mode):
            raise MillefeuilleContractError(
                f"{label} path contains a symbolic link or non-directory: {target}"
            )
        if key not in directories:
            directories[key] = _open_relative_directory_fd(current, part)
        child = directories[key]
        if _stat_object_identity(named) != _stat_object_identity(os.fstat(child)):
            raise MillefeuilleContractError(f"{label} directory changed: {target}")
        current = child
    return os.dup(current)


def _open_parent_directory_fd(path: Path, *, label: str) -> tuple[int, str]:
    if not _supports_no_follow():
        raise MillefeuilleContractError(
            f"{label} requires no-follow filesystem reads on this platform"
        )

    target = Path(path)
    anchor = Path(target.anchor) if target.is_absolute() else Path(".")
    parts = target.parts[1:] if target.is_absolute() else target.parts
    if not parts or parts[-1] in {"", ".", ".."}:
        raise MillefeuilleContractError(f"{label} is not a regular file: {target}")

    directories = _DIRECTORY_READ_SCOPE.get()
    if directories is not None and target.is_absolute() and ".." not in parts:
        try:
            return _held_parent_directory_fd(
                target, label=label, directories=directories
            ), parts[-1]
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not open {label} path {target}: {exc}"
            ) from exc

    current_fd = _open_directory_fd(anchor)
    try:
        for part in parts[:-1]:
            if part in {"", "."}:
                continue
            if part == "..":
                raise MillefeuilleContractError(
                    f"{label} path must not contain parent traversal: {target}"
                )
            next_fd = _open_relative_directory_fd(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except MillefeuilleContractError:
        os.close(current_fd)
        raise
    except FileNotFoundError as exc:
        os.close(current_fd)
        raise MillefeuilleContractError(f"{label} not found: {target}") from exc
    except OSError as exc:
        os.close(current_fd)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or non-directories: "
                f"{target}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open {label} path {target}: {exc}"
        ) from exc


def _open_regular_file_fd(
    path: Path,
    *,
    label: str,
) -> tuple[int, int, str, os.stat_result]:
    parent_fd, name = _open_parent_directory_fd(path, label=label)
    flags = _regular_file_read_flags(no_follow=True)
    try:
        # Reject named nonregular entries before opening, including under a
        # trusted guard that refuses FIFO descriptors independently of us.
        # Keep O_NONBLOCK and the post-open identity checks for substitution races.
        named_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(named_before.st_mode):
            raise MillefeuilleContractError(
                f"{label} must not be a symbolic link: {path}"
            )
        if not stat.S_ISREG(named_before.st_mode):
            raise MillefeuilleContractError(f"{label} is not a regular file: {path}")
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        os.close(parent_fd)
        raise MillefeuilleContractError(f"{label} not found: {path}") from exc
    except OSError as exc:
        os.close(parent_fd)
        if exc.errno == errno.ELOOP:
            raise MillefeuilleContractError(
                f"{label} must not be a symbolic link: {path}"
            ) from exc
        raise MillefeuilleContractError(
            f"could not open {label} {path}: {exc}"
        ) from exc
    except BaseException:
        os.close(parent_fd)
        raise

    try:
        opened_stat = os.fstat(fd)
        named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(opened_stat.st_mode) or not stat.S_ISREG(
            named_stat.st_mode
        ):
            raise MillefeuilleContractError(f"{label} is not a regular file: {path}")
        if _stat_object_identity(opened_stat) != _stat_object_identity(named_stat):
            raise MillefeuilleContractError(f"{label} changed while opening: {path}")
        return parent_fd, fd, name, opened_stat
    except Exception:
        os.close(fd)
        os.close(parent_fd)
        raise


def _stat_object_identity(value: os.stat_result) -> _StatObjectIdentity:
    """Return fields that bind a path and descriptor to the same object."""

    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
    )


def _stat_stability_snapshot(value: os.stat_result) -> _StatStabilitySnapshot:
    """Return fields that reveal mutation without read-side timestamp noise.

    POSIX ``ctime`` is a mutation signal and remains part of the snapshot.
    Windows exposes creation/change timestamps inconsistently between path and
    descriptor stats, and may change descriptor-side ``ctime`` merely because
    the descriptor was read. Size and mtime checks remain active there.
    """

    snapshot = (
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
    )
    if not _WINDOWS:
        return (*snapshot, value.st_ctime_ns)
    return snapshot


def _stat_record(value: os.stat_result) -> _StatRecord:
    return _stat_object_identity(value), _stat_stability_snapshot(value)


def _has_stable_object_identity(value: os.stat_result) -> bool:
    """Whether replacement checks can distinguish this filesystem object."""

    return value.st_ino != 0


def _is_windows_reparse_point(value: os.stat_result) -> bool:
    """Detect Windows junctions and other reparse-backed path components."""

    if not _WINDOWS:
        return False
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_tag = getattr(value, "st_reparse_tag", 0)
    return bool(attributes & _WINDOWS_REPARSE_POINT) or bool(reparse_tag)


def _require_portable_directory(
    path: Path,
    *,
    target: Path,
    label: str,
) -> None:
    """Require one checked directory in a portable read path."""

    try:
        directory_stat = os.lstat(path)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {target}") from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} path {target}: {exc}"
        ) from exc
    if (
        stat.S_ISLNK(directory_stat.st_mode)
        or _is_windows_reparse_point(directory_stat)
        or not stat.S_ISDIR(directory_stat.st_mode)
    ):
        raise MillefeuilleContractError(
            f"{label} path must not contain symbolic links, reparse points, "
            f"or non-directories: {target}"
        )


def _portable_lstat_regular_file(path: Path, *, label: str) -> os.stat_result:
    """Reject symlinks/non-directories before a portable regular-file open."""

    target = Path(path)
    anchor = Path(target.anchor) if target.is_absolute() else Path(".")
    parts = target.parts[1:] if target.is_absolute() else target.parts
    if not parts or parts[-1] in {"", ".", ".."}:
        raise MillefeuilleContractError(f"{label} is not a regular file: {target}")

    _require_portable_directory(anchor, target=target, label=label)
    current = anchor
    for part in parts[:-1]:
        if part in {"", "."}:
            continue
        if part == "..":
            raise MillefeuilleContractError(
                f"{label} path must not contain parent traversal: {target}"
            )
        current /= part
        _require_portable_directory(current, target=target, label=label)

    try:
        named_stat = os.lstat(target)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {target}") from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} {target}: {exc}"
        ) from exc
    if stat.S_ISLNK(named_stat.st_mode) or _is_windows_reparse_point(named_stat):
        raise MillefeuilleContractError(
            f"{label} must not be a symbolic link or reparse point: {target}"
        )
    if not stat.S_ISREG(named_stat.st_mode):
        raise MillefeuilleContractError(f"{label} is not a regular file: {target}")
    return named_stat


def _open_windows_locked_regular_file_fd(
    path: Path,
    *,
    label: str,
    expected_stat: os.stat_result,
) -> tuple[int, os.stat_result]:
    """Open a Windows read handle that excludes concurrent writers/deleters."""

    import ctypes
    from ctypes import wintypes
    import msvcrt

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        os.fspath(path),
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle_value = ctypes.c_void_p(-1).value
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        detail = ctypes.FormatError(error).strip()
        raise MillefeuilleContractError(f"could not open {label} {path}: {detail}")

    crt_flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        crt_flags |= os.O_BINARY
    if hasattr(os, "O_NOINHERIT"):
        crt_flags |= os.O_NOINHERIT
    try:
        fd = msvcrt.open_osfhandle(handle, crt_flags)
    except OSError as exc:
        close_handle(handle)
        raise MillefeuilleContractError(
            f"could not bind {label} handle {path}: {exc}"
        ) from exc

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise MillefeuilleContractError(f"{label} is not a regular file: {path}")
        expected_identity = _stat_object_identity(expected_stat)
        expected_snapshot = _stat_stability_snapshot(expected_stat)
        if (
            not _has_stable_object_identity(opened_stat)
            or _stat_object_identity(opened_stat) != expected_identity
            or _stat_stability_snapshot(opened_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while opening: {path}")
        named_stat = _portable_lstat_regular_file(path, label=label)
        if (
            not _has_stable_object_identity(named_stat)
            or _stat_object_identity(named_stat) != expected_identity
            or _stat_stability_snapshot(named_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while opening: {path}")
        return fd, opened_stat
    except Exception:
        os.close(fd)
        raise


def _open_portable_regular_file_fd(
    path: Path,
    *,
    label: str,
) -> tuple[int, os.stat_result]:
    """Open a portable regular file after lstat checks, then bind its identity."""

    target = Path(path)
    expected_stat = _portable_lstat_regular_file(target, label=label)
    if not _has_stable_object_identity(expected_stat):
        raise MillefeuilleContractError(
            f"{label} filesystem does not expose stable file identity: {target}"
        )
    if _WINDOWS:
        return _open_windows_locked_regular_file_fd(
            target,
            label=label,
            expected_stat=expected_stat,
        )
    flags = _regular_file_read_flags(no_follow=False)
    try:
        fd = os.open(target, flags)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {target}") from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not open {label} {target}: {exc}"
        ) from exc

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise MillefeuilleContractError(f"{label} is not a regular file: {target}")
        expected_identity = _stat_object_identity(expected_stat)
        expected_snapshot = _stat_stability_snapshot(expected_stat)
        if (
            not _has_stable_object_identity(opened_stat)
            or _stat_object_identity(opened_stat) != expected_identity
            or _stat_stability_snapshot(opened_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while opening: {target}")
        named_stat = _portable_lstat_regular_file(target, label=label)
        if (
            not _has_stable_object_identity(named_stat)
            or _stat_object_identity(named_stat) != expected_identity
            or _stat_stability_snapshot(named_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while opening: {target}")
        return fd, opened_stat
    except Exception:
        os.close(fd)
        raise


class RootArtifactReader:
    """Pinned-root reader for descriptor-relative batch artifact inspection."""

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        self._root_fd: int | None = None
        self._root_identity: tuple[int, int] | None = None
        self.budget_identity = None
        self._file_snapshots: dict[Path, tuple[str, _StatRecord]] = {}
        self._missing_file_snapshots: dict[Path, str] = {}
        self._directory_snapshots: dict[
            Path,
            tuple[
                str,
                _StatRecord,
                tuple[tuple[str, _StatRecord], ...],
                int,
            ],
        ] = {}

    def __enter__(self) -> RootArtifactReader:
        if self._root_fd is not None:
            raise MillefeuilleContractError("source-pack reader is already open")
        self.budget_identity = _READ_BUDGET.get()
        _debit_metadata(opens=len(self.root.parts) + 1, read_bytes=1)
        self._root_fd = _open_directory_path_no_follow(
            self.root,
            label="source-pack root",
        )
        root_stat = os.fstat(self._root_fd)
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._file_snapshots.clear()
        self._missing_file_snapshots.clear()
        self._directory_snapshots.clear()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    @property
    def root_identity(self) -> tuple[int, int]:
        self._require_open()
        assert self._root_identity is not None
        return self._root_identity

    def verify_root(self) -> None:
        self._require_open()
        _debit_metadata(opens=len(self.root.parts) + 1, read_bytes=1)
        candidate_fd = _open_directory_path_no_follow(
            self.root,
            label="source-pack root",
        )
        try:
            candidate_stat = os.fstat(candidate_fd)
            if (candidate_stat.st_dev, candidate_stat.st_ino) != self.root_identity:
                raise MillefeuilleContractError(
                    f"source-pack root changed during batch retrieval: {self.root}"
                )
        finally:
            os.close(candidate_fd)

    def revalidate_snapshot(self) -> None:
        """Fail if any preflighted input changed since it was inspected."""

        self.verify_root()
        for relative, (label, expected) in sorted(
            self._file_snapshots.items(),
            key=lambda item: item[0].as_posix(),
        ):
            target = self.root / relative
            parent_fd, fd, name, opened_stat, _target = self._open_regular_file(
                target,
                label=label,
            )
            try:
                self._require_unchanged_regular_file(
                    parent_fd=parent_fd,
                    fd=fd,
                    name=name,
                    path=target,
                    label=label,
                    expected_stat=opened_stat,
                )
                if _stat_record(opened_stat) != expected:
                    raise MillefeuilleContractError(
                        f"{label} changed after batch preflight: {target}"
                    )
            finally:
                os.close(fd)
                os.close(parent_fd)

        for relative, label in sorted(
            self._missing_file_snapshots.items(),
            key=lambda item: item[0].as_posix(),
        ):
            target = self.root / relative
            try:
                parent_fd, fd, _name, _opened_stat, _target = self._open_regular_file(
                    target,
                    label=label,
                )
            except MillefeuilleContractError as exc:
                if f"{label} not found:" in str(exc):
                    continue
                raise MillefeuilleContractError(
                    f"{label} changed after batch preflight: {target}"
                ) from exc
            else:
                os.close(fd)
                os.close(parent_fd)
                raise MillefeuilleContractError(
                    f"{label} appeared after batch preflight: {target}"
                )

        for relative, (label, expected_stat, expected_entries, entry_limit) in sorted(
            self._directory_snapshots.items(),
            key=lambda item: item[0].as_posix(),
        ):
            target = self.root / relative
            directory_fd = self._open_relative_directory(relative, label=label)
            try:
                _debit_metadata(opens=1, read_bytes=1)
                current_stat = _stat_record(os.fstat(directory_fd))
                current_entries = self._directory_entries(
                    directory_fd,
                    path=target,
                    label=label,
                    entry_limit=entry_limit,
                )
                if current_stat != expected_stat or current_entries != expected_entries:
                    raise MillefeuilleContractError(
                        f"{label} changed after batch preflight: {target}"
                    )
            finally:
                os.close(directory_fd)

    def verify_regular_file(self, path: str | Path, label: str) -> None:
        parent_fd, fd, name, opened_stat, target = self._open_regular_file(
            path,
            label=label,
        )
        try:
            self._require_unchanged_regular_file(
                parent_fd=parent_fd,
                fd=fd,
                name=name,
                path=target,
                label=label,
                expected_stat=opened_stat,
            )
            self._remember_regular_file(target, label, opened_stat)
        finally:
            os.close(fd)
            os.close(parent_fd)

    def probe_regular_file(self, path: str | Path, label: str) -> bool:
        try:
            self.verify_regular_file(path, label)
        except MillefeuilleContractError as exc:
            if f"{label} not found:" in str(exc):
                self._remember_missing_regular_file(path, label)
                return False
            raise
        return True

    def read_bytes(
        self,
        path: str | Path,
        label: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        max_bytes = _validated_max_bytes(max_bytes, label=label)
        parent_fd, fd, name, opened_stat, target = self._open_regular_file(
            path,
            label=label,
        )
        try:
            _debit_metadata(read_bytes=opened_stat.st_size + 1)
            payload = _read_opened_file_bytes(
                fd,
                opened_stat,
                path=target,
                label=label,
                max_bytes=min(max_bytes, opened_stat.st_size)
                if max_bytes is not None
                else opened_stat.st_size,
            )
            self._require_unchanged_regular_file(
                parent_fd=parent_fd,
                fd=fd,
                name=name,
                path=target,
                label=label,
                expected_stat=opened_stat,
            )
            self._remember_regular_file(target, label, opened_stat)
            return payload
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not read {label} {target}: {exc}"
            ) from exc
        finally:
            os.close(fd)
            os.close(parent_fd)

    def read_text(
        self,
        path: str | Path,
        label: str,
        *,
        max_bytes: int | None = None,
    ) -> str:
        target = self._target(path, label=label)
        try:
            return self.read_bytes(target, label, max_bytes=max_bytes).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MillefeuilleContractError(
                f"{label} is not valid UTF-8: {target}"
            ) from exc

    def load_json_object(
        self,
        path: str | Path,
        label: str,
        *,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        target = self._target(path, label=label)
        try:
            payload = json.loads(self.read_text(target, label, max_bytes=max_bytes))
        except json.JSONDecodeError as exc:
            raise MillefeuilleContractError(
                f"{label} is not valid JSON: {target}"
            ) from exc
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(f"{label} must be an object")
        return payload

    def require_budget_capability(self, root):
        budget = _READ_BUDGET.get()
        if (
            budget is None
            or self.budget_identity is not budget
            or Path(root) != self.root
        ):
            raise MillefeuilleContractError(
                "shared planning reader capability required"
            )
        budget.debit()
        self._require_open()

    def read_before_image(self, path, label, *, max_bytes):
        self.require_budget_capability(self.root)
        if not self.probe_regular_file(path, label):
            return None, None
        parent_fd, fd, name, metadata, target = self._open_regular_file(
            path, label=label
        )
        try:
            if metadata.st_size > max_bytes:
                raise MillefeuilleContractError("before-image exceeds planning bound")
            _debit_metadata(read_bytes=metadata.st_size + 1)
            before = _read_opened_file_bytes(
                fd, metadata, path=target, label=label, max_bytes=metadata.st_size
            )
            self._require_unchanged_regular_file(
                parent_fd=parent_fd,
                fd=fd,
                name=name,
                path=target,
                label=label,
                expected_stat=metadata,
            )
            self._remember_regular_file(target, label, metadata)
            return before, metadata
        finally:
            os.close(fd)
            os.close(parent_fd)

    def list_directory_names(
        self, path: str | Path, label: str, *, entry_limit: int = 512
    ) -> list[str]:
        entry_limit = _entry_limit(entry_limit)
        relative = self._relative(path, label=label)
        previous = self._directory_snapshots.get(relative)
        if previous is not None and previous[3] != entry_limit:
            raise MillefeuilleContractError("directory snapshot entry_limit changed")
        directory_fd = self._open_relative_directory(relative, label=label)
        try:
            _debit_metadata(opens=1, read_bytes=1)
            before_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(before_stat.st_mode):
                raise MillefeuilleContractError(f"{label} is not a directory")
            entries = self._directory_entries(
                directory_fd,
                path=self.root / relative,
                label=label,
                entry_limit=entry_limit,
            )
            _debit_metadata(opens=1, read_bytes=1)
            after_stat = os.fstat(directory_fd)
            if _stat_record(before_stat) != _stat_record(after_stat):
                raise MillefeuilleContractError(
                    f"{label} changed while listed: {self.root / relative}"
                )
            snapshot = (label, _stat_record(after_stat), entries, entry_limit)
            previous = self._directory_snapshots.get(relative)
            if previous is not None and previous != snapshot:
                raise MillefeuilleContractError(
                    f"{label} changed after first inspection: {self.root / relative}"
                )
            self._directory_snapshots[relative] = snapshot
            return [name for name, _identity in entries]
        finally:
            os.close(directory_fd)

    def is_directory(self, path: str | Path, label: str) -> bool:
        relative = self._relative(path, label=label)
        try:
            directory_fd = self._open_relative_directory(relative, label=label)
        except MillefeuilleContractError as exc:
            if f"{label} not found:" in str(exc) or "non-directories" in str(exc):
                return False
            raise
        try:
            return stat.S_ISDIR(os.fstat(directory_fd).st_mode)
        finally:
            os.close(directory_fd)

    def _require_open(self) -> int:
        if self._root_fd is None:
            raise MillefeuilleContractError("source-pack reader is not open")
        return self._root_fd

    def _remember_regular_file(
        self,
        path: str | Path,
        label: str,
        opened_stat: os.stat_result,
    ) -> None:
        target = self._target(path, label=label)
        relative = self._relative(target, label=label)
        if relative in self._missing_file_snapshots:
            raise MillefeuilleContractError(
                f"{label} appeared after first inspection: {target}"
            )
        snapshot = (label, _stat_record(opened_stat))
        previous = self._file_snapshots.get(relative)
        if previous is not None and previous[1] != snapshot[1]:
            raise MillefeuilleContractError(
                f"{label} changed after first inspection: {target}"
            )
        self._file_snapshots[relative] = snapshot

    def _remember_missing_regular_file(
        self,
        path: str | Path,
        label: str,
    ) -> None:
        target = self._target(path, label=label)
        relative = self._relative(target, label=label)
        if relative in self._file_snapshots:
            raise MillefeuilleContractError(
                f"{label} disappeared after first inspection: {target}"
            )
        self._missing_file_snapshots.setdefault(relative, label)

    @staticmethod
    def _directory_entries(
        directory_fd: int,
        *,
        path: Path,
        label: str,
        entry_limit: int = 512,
    ) -> tuple[tuple[str, _StatRecord], ...]:
        entry_limit = _entry_limit(entry_limit)
        try:
            # Bound initial enumeration and every snapshot revalidation alike.
            # At most entry_limit entries are retained, including revalidation.

            budget = _READ_BUDGET.get()
            if budget is not None:
                budget.debit(opens=1, scans=1)
            entries = []
            with os.scandir(directory_fd) as iterator:
                while True:
                    if budget is not None:
                        budget.debit(read_bytes=1, scans=1)
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    if len(entries) >= entry_limit:
                        raise MillefeuilleContractError(
                            f"{label} directory inventory exceeds {entry_limit} entries"
                        )
                    if budget is not None:
                        budget.debit(read_bytes=1, opens=1)
                    entries.append(
                        (entry.name, _stat_record(entry.stat(follow_symlinks=False)))
                    )
            return tuple(sorted(entries, key=lambda item: item[0]))
        except FileNotFoundError as exc:
            raise MillefeuilleContractError(
                f"{label} changed while listed: {path}"
            ) from exc
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not list {label} {path}: {exc}"
            ) from exc

    def _target(self, path: str | Path, *, label: str) -> Path:
        target = Path(path).absolute()
        self._relative(target, label=label)
        return target

    def _relative(self, path: str | Path, *, label: str) -> Path:
        target = Path(path).absolute()
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise MillefeuilleContractError(
                f"{label} escapes the source-pack root: {target}"
            ) from exc
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise MillefeuilleContractError(
                f"{label} has an unsafe source-pack-relative path: {target}"
            )
        return relative

    def _open_relative_directory(self, relative: Path, *, label: str) -> int:
        _debit_metadata(opens=1)
        current_fd = os.dup(self._require_open())
        try:
            for part in relative.parts:
                _debit_metadata(opens=2, read_bytes=1)
                next_fd = _open_relative_directory_fd(current_fd, part)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except FileNotFoundError as exc:
            os.close(current_fd)
            raise MillefeuilleContractError(
                f"{label} not found: {self.root / relative}"
            ) from exc
        except OSError as exc:
            os.close(current_fd)
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise MillefeuilleContractError(
                    f"{label} path must not contain symbolic links or "
                    f"non-directories: {self.root / relative}"
                ) from exc
            raise MillefeuilleContractError(
                f"could not open {label} {self.root / relative}: {exc}"
            ) from exc

    def _open_regular_file(
        self,
        path: str | Path,
        *,
        label: str,
    ) -> tuple[int, int, str, os.stat_result, Path]:
        target = self._target(path, label=label)
        relative = self._relative(target, label=label)
        if not relative.parts:
            raise MillefeuilleContractError(f"{label} is not a regular file: {target}")
        parent_relative = Path(*relative.parts[:-1])
        parent_fd = self._open_relative_directory(parent_relative, label=label)
        name = relative.parts[-1]
        flags = _regular_file_read_flags(no_follow=True)
        try:
            _debit_metadata(opens=1)
            fd = os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            os.close(parent_fd)
            raise MillefeuilleContractError(f"{label} not found: {target}") from exc
        except OSError as exc:
            os.close(parent_fd)
            if exc.errno == errno.ELOOP:
                raise MillefeuilleContractError(
                    f"{label} must not be a symbolic link: {target}"
                ) from exc
            raise MillefeuilleContractError(
                f"could not open {label} {target}: {exc}"
            ) from exc
        except Exception:
            os.close(parent_fd)
            raise
        try:
            _debit_metadata(opens=1, read_bytes=1)
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise MillefeuilleContractError(
                    f"{label} is not a regular file: {target}"
                )
            self._require_unchanged_regular_file(
                parent_fd=parent_fd,
                fd=fd,
                name=name,
                path=target,
                label=label,
                expected_stat=opened_stat,
            )
            return parent_fd, fd, name, opened_stat, target
        except Exception:
            os.close(fd)
            os.close(parent_fd)
            raise

    @staticmethod
    def _require_unchanged_regular_file(
        *,
        parent_fd: int,
        fd: int,
        name: str,
        path: Path,
        label: str,
        expected_stat: os.stat_result,
    ) -> None:
        _debit_metadata(opens=2, read_bytes=2)
        fd_stat = os.fstat(fd)
        try:
            named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise MillefeuilleContractError(
                f"{label} changed while read: {path}"
            ) from exc
        expected_identity = _stat_object_identity(expected_stat)
        expected_snapshot = _stat_stability_snapshot(expected_stat)
        if (
            _stat_object_identity(fd_stat) != expected_identity
            or _stat_stability_snapshot(fd_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while read: {path}")
        if (
            _stat_object_identity(named_stat) != expected_identity
            or _stat_stability_snapshot(named_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while read: {path}")


def verify_regular_file_no_follow(path: str | Path, label: str) -> None:
    """Verify one regular file, using no-follow descriptors when available."""

    target = Path(path)
    if not _supports_no_follow():
        # Preserve the portable single-run path while retaining explicit
        # symlink rejection and binding the opened descriptor to the lstat
        # identity. Batch retrieval never reaches this fallback:
        # RootArtifactReader fails closed at its boundary.
        fd, _opened_stat = _open_portable_regular_file_fd(target, label=label)
        os.close(fd)
        return

    parent_fd, fd, name, opened_stat = _open_regular_file_fd(target, label=label)
    try:
        final_fd_stat = os.fstat(fd)
        final_named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected_identity = _stat_object_identity(opened_stat)
        expected_snapshot = _stat_stability_snapshot(opened_stat)
        if (
            _stat_object_identity(final_fd_stat) != expected_identity
            or _stat_stability_snapshot(final_fd_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(
                f"{label} changed while being verified: {target}"
            )
        if (
            _stat_object_identity(final_named_stat) != expected_identity
            or _stat_stability_snapshot(final_named_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(
                f"{label} changed while being verified: {target}"
            )
    finally:
        os.close(fd)
        os.close(parent_fd)


def read_bytes_no_follow(
    path: str | Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read stable bytes with no-follow descriptors when the platform supports it."""

    max_bytes = _validated_max_bytes(max_bytes, label=label)
    target = Path(path)
    if not _supports_no_follow():
        try:
            fd, opened_stat = _open_portable_regular_file_fd(
                target,
                label=label,
            )
        except MillefeuilleContractError as exc:
            raise MillefeuilleContractError(
                f"could not read {label} {target}: {exc}"
            ) from exc
        try:
            payload = _read_opened_file_bytes(
                fd,
                opened_stat,
                path=target,
                label=label,
                max_bytes=max_bytes,
            )
            final_fd_stat = os.fstat(fd)
            final_named_stat = _portable_lstat_regular_file(target, label=label)
            expected_identity = _stat_object_identity(opened_stat)
            expected_snapshot = _stat_stability_snapshot(opened_stat)
            if (
                _stat_object_identity(final_fd_stat) != expected_identity
                or _stat_stability_snapshot(final_fd_stat) != expected_snapshot
            ):
                raise MillefeuilleContractError(f"{label} changed while read: {target}")
            if (
                _stat_object_identity(final_named_stat) != expected_identity
                or _stat_stability_snapshot(final_named_stat) != expected_snapshot
            ):
                raise MillefeuilleContractError(f"{label} changed while read: {target}")
            return payload
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not read {label} {target}: {exc}"
            ) from exc
        finally:
            os.close(fd)

    try:
        parent_fd, fd, name, opened_stat = _open_regular_file_fd(
            target,
            label=label,
        )
    except MillefeuilleContractError as exc:
        raise MillefeuilleContractError(
            f"could not read {label} {target}: {exc}"
        ) from exc
    try:
        payload = _read_opened_file_bytes(
            fd,
            opened_stat,
            path=target,
            label=label,
            max_bytes=max_bytes,
        )
        final_fd_stat = os.fstat(fd)
        final_named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected_identity = _stat_object_identity(opened_stat)
        expected_snapshot = _stat_stability_snapshot(opened_stat)
        if (
            _stat_object_identity(final_fd_stat) != expected_identity
            or _stat_stability_snapshot(final_fd_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while read: {target}")
        if (
            _stat_object_identity(final_named_stat) != expected_identity
            or _stat_stability_snapshot(final_named_stat) != expected_snapshot
        ):
            raise MillefeuilleContractError(f"{label} changed while read: {target}")
        return payload
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {label} {target}: {exc}"
        ) from exc
    finally:
        os.close(fd)
        os.close(parent_fd)


def copy_verified_file_no_follow(
    path, destination_fd, *, expected_sha256, max_bytes, check_deadline
):
    """Stream complete stable bytes to private staging; caller discards on failure.

    64 KiB transient chunks; no portable write fallback. Deadline checks between
    regular-file operations are cooperative, not hard host I/O termination.
    """
    import hashlib
    import re

    if (
        type(max_bytes) is not int
        or not 0 < max_bytes <= 16777216
        or type(expected_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or not _supports_no_follow()
    ):
        raise MillefeuilleContractError("unsupported streaming source policy")
    check_deadline()
    parent_fd, fd, name, opened = _open_regular_file_fd(Path(path), label="native PDF")
    try:
        if not 0 < opened.st_size <= max_bytes:
            raise ValueError("source-size-out-of-bounds")
        destination = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(destination.st_mode)
            or destination.st_size != 0
            or os.lseek(destination_fd, 0, os.SEEK_CUR) != 0
            or _stat_object_identity(destination) == _stat_object_identity(opened)
        ):
            raise ValueError("invalid-staging-file")
        hasher = hashlib.sha256()
        size = 0
        while size < opened.st_size:
            check_deadline()
            chunk = os.read(fd, min(65536, opened.st_size - size))
            if not chunk:
                raise ValueError("short-source-read")
            hasher.update(chunk)
            offset = 0
            while offset < len(chunk):
                check_deadline()
                written = os.write(destination_fd, memoryview(chunk)[offset:])
                if written <= 0:
                    raise ValueError("short-staging-write")
                offset += written
            size += len(chunk)
        for final in (
            os.fstat(fd),
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False),
        ):
            if _stat_object_identity(final) != _stat_object_identity(
                opened
            ) or _stat_stability_snapshot(final) != _stat_stability_snapshot(opened):
                raise ValueError("source-identity-drift")
        check_deadline()
        digest = hasher.hexdigest()
        if digest != expected_sha256:
            raise ValueError("source-hash-drift")
        return size, digest
    finally:
        os.close(fd)
        os.close(parent_fd)


def read_text_no_follow(
    path: str | Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> str:
    target = Path(path)
    try:
        return read_bytes_no_follow(target, label, max_bytes=max_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            f"{label} is not valid UTF-8: {target}"
        ) from exc


def load_json_object_no_follow(
    path: str | Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    target = Path(path)
    try:
        payload = json.loads(read_text_no_follow(target, label, max_bytes=max_bytes))
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON: {target}") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return payload


def write_new_text_no_follow(
    path: str | Path,
    text: str,
    label: str,
    *,
    forbidden_ancestor_markers: frozenset[str] = frozenset(),
    expected_parent_identity: tuple[int, int] | None = None,
    preserve_created_on_failure: bool = False,
) -> None:
    """Create one new UTF-8 file through pinned no-follow descriptors.

    Existing outputs are never replaced. Missing parent directories are made
    descriptor-relatively, and the parent plus final name are rebound after the
    write so a concurrent rename or symlink substitution fails closed.
    Callers requiring operator resolution can preserve created entries on
    failure instead of attempting name-based cleanup.
    """

    if not _supports_no_follow():
        raise MillefeuilleContractError(
            f"{label} requires no-follow filesystem writes on this platform"
        )
    target = Path(path)
    if target.name in {"", ".", ".."}:
        raise MillefeuilleContractError(f"{label} is not a regular file: {target}")
    payload = text.encode("utf-8")
    parent_fd = _open_or_create_directory_path_no_follow(
        target.parent,
        label=f"{label} parent",
        forbidden_ancestor_markers=forbidden_ancestor_markers,
    )
    parent_identity = os.fstat(parent_fd)
    if (
        expected_parent_identity is not None
        and (
            parent_identity.st_dev,
            parent_identity.st_ino,
        )
        != expected_parent_identity
    ):
        os.close(parent_fd)
        raise MillefeuilleContractError(
            f"{label} parent directory changed after approval: {target.parent}"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd: int | None = None
    opened_identity: tuple[int, int] | None = None
    try:
        try:
            fd = os.open(target.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise MillefeuilleContractError(
                f"{label} already exists or is a symbolic link: {target}"
            ) from exc
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ELOOP}:
                raise MillefeuilleContractError(
                    f"{label} already exists or is a symbolic link: {target}"
                ) from exc
            raise MillefeuilleContractError(
                f"could not create {label} {target}: {exc}"
            ) from exc

        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
            raise MillefeuilleContractError(
                f"{label} is not a singly linked regular file: {target}"
            )
        opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise MillefeuilleContractError(
                    f"could not write complete {label}: {target}"
                )
            offset += written
        os.fsync(fd)

        final_fd_stat = os.fstat(fd)
        final_named_stat = os.stat(
            target.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_fd_stat.st_mode)
            or final_fd_stat.st_nlink != 1
            or (final_fd_stat.st_dev, final_fd_stat.st_ino) != opened_identity
            or (final_named_stat.st_dev, final_named_stat.st_ino) != opened_identity
            or final_fd_stat.st_size != len(payload)
        ):
            raise MillefeuilleContractError(
                f"{label} changed while being written: {target}"
            )

        rebound_parent_fd = _open_directory_path_no_follow(
            target.parent,
            label=f"{label} parent",
            forbidden_ancestor_markers=forbidden_ancestor_markers,
        )
        try:
            rebound_parent = os.fstat(rebound_parent_fd)
            rebound_named = os.stat(
                target.name,
                dir_fd=rebound_parent_fd,
                follow_symlinks=False,
            )
            if (rebound_parent.st_dev, rebound_parent.st_ino) != (
                parent_identity.st_dev,
                parent_identity.st_ino,
            ) or (rebound_named.st_dev, rebound_named.st_ino) != opened_identity:
                raise MillefeuilleContractError(
                    f"{label} path changed while being written: {target}"
                )
        finally:
            os.close(rebound_parent_fd)
        os.fsync(parent_fd)
    except Exception as exc:
        if (
            not preserve_created_on_failure
            and fd is not None
            and opened_identity is not None
        ):
            try:
                named_stat = os.stat(
                    target.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if (named_stat.st_dev, named_stat.st_ino) == opened_identity:
                    os.unlink(target.name, dir_fd=parent_fd)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise MillefeuilleContractError(
                f"could not safely write {label} {target}: {exc}"
            ) from exc
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)
