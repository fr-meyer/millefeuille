"""Descriptor-relative, no-follow reads for local Millefeuille artifacts."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
from types import TracebackType
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError


def _supports_no_follow() -> bool:
    return hasattr(os, "O_NOFOLLOW")


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


def _open_directory_path_no_follow(path: Path, *, label: str) -> int:
    if not _supports_no_follow():
        raise MillefeuilleContractError(
            f"{label} requires no-follow filesystem reads on this platform"
        )
    target = Path(path)
    anchor = Path(target.anchor) if target.is_absolute() else Path(".")
    parts = target.parts[1:] if target.is_absolute() else target.parts
    current_fd = _open_directory_fd(anchor)
    try:
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
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
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

    try:
        opened_stat = os.fstat(fd)
        named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(opened_stat.st_mode) or not stat.S_ISREG(
            named_stat.st_mode
        ):
            raise MillefeuilleContractError(f"{label} not found: {path}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (
            named_stat.st_dev,
            named_stat.st_ino,
        ):
            raise MillefeuilleContractError(f"{label} changed while opening: {path}")
        return parent_fd, fd, name, opened_stat
    except Exception:
        os.close(fd)
        os.close(parent_fd)
        raise


def _stable_stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class RootArtifactReader:
    """Pinned-root reader for descriptor-relative batch artifact inspection."""

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        self._root_fd: int | None = None
        self._root_identity: tuple[int, int] | None = None
        self._file_snapshots: dict[Path, tuple[str, tuple[int, ...]]] = {}
        self._missing_file_snapshots: dict[Path, str] = {}
        self._directory_snapshots: dict[
            Path,
            tuple[
                str,
                tuple[int, ...],
                tuple[tuple[str, tuple[int, ...]], ...],
            ],
        ] = {}

    def __enter__(self) -> RootArtifactReader:
        if self._root_fd is not None:
            raise MillefeuilleContractError("source-pack reader is already open")
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
                if _stable_stat_identity(opened_stat) != expected:
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

        for relative, (label, expected_stat, expected_entries) in sorted(
            self._directory_snapshots.items(),
            key=lambda item: item[0].as_posix(),
        ):
            target = self.root / relative
            directory_fd = self._open_relative_directory(relative, label=label)
            try:
                current_stat = _stable_stat_identity(os.fstat(directory_fd))
                current_entries = self._directory_entries(
                    directory_fd,
                    path=target,
                    label=label,
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

    def read_bytes(self, path: str | Path, label: str) -> bytes:
        parent_fd, fd, name, opened_stat, target = self._open_regular_file(
            path,
            label=label,
        )
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            payload = b"".join(chunks)
            self._require_unchanged_regular_file(
                parent_fd=parent_fd,
                fd=fd,
                name=name,
                path=target,
                label=label,
                expected_stat=opened_stat,
            )
            if len(payload) != opened_stat.st_size:
                raise MillefeuilleContractError(
                    f"{label} changed while read: {target}"
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

    def read_text(self, path: str | Path, label: str) -> str:
        target = self._target(path, label=label)
        try:
            return self.read_bytes(target, label).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MillefeuilleContractError(
                f"{label} is not valid UTF-8: {target}"
            ) from exc

    def load_json_object(self, path: str | Path, label: str) -> dict[str, Any]:
        target = self._target(path, label=label)
        try:
            payload = json.loads(self.read_text(target, label))
        except json.JSONDecodeError as exc:
            raise MillefeuilleContractError(
                f"{label} is not valid JSON: {target}"
            ) from exc
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(f"{label} must be an object")
        return payload

    def list_directory_names(self, path: str | Path, label: str) -> list[str]:
        relative = self._relative(path, label=label)
        directory_fd = self._open_relative_directory(relative, label=label)
        try:
            before_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(before_stat.st_mode):
                raise MillefeuilleContractError(f"{label} is not a directory")
            entries = self._directory_entries(
                directory_fd,
                path=self.root / relative,
                label=label,
            )
            after_stat = os.fstat(directory_fd)
            if _stable_stat_identity(before_stat) != _stable_stat_identity(after_stat):
                raise MillefeuilleContractError(
                    f"{label} changed while listed: {self.root / relative}"
                )
            snapshot = (label, _stable_stat_identity(after_stat), entries)
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
        snapshot = (label, _stable_stat_identity(opened_stat))
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
    ) -> tuple[tuple[str, tuple[int, ...]], ...]:
        try:
            names = sorted(os.listdir(directory_fd))
            return tuple(
                (
                    name,
                    _stable_stat_identity(
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    ),
                )
                for name in names
            )
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
        current_fd = os.dup(self._require_open())
        try:
            for part in relative.parts:
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
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
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
        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise MillefeuilleContractError(f"{label} not found: {target}")
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
        fd_stat = os.fstat(fd)
        try:
            named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise MillefeuilleContractError(
                f"{label} changed while read: {path}"
            ) from exc
        expected = _stable_stat_identity(expected_stat)
        if _stable_stat_identity(fd_stat) != expected:
            raise MillefeuilleContractError(f"{label} changed while read: {path}")
        if _stable_stat_identity(named_stat) != expected:
            raise MillefeuilleContractError(f"{label} changed while read: {path}")


def verify_regular_file_no_follow(path: str | Path, label: str) -> None:
    """Verify one regular file, using no-follow descriptors when available."""

    target = Path(path)
    if not _supports_no_follow():
        # Preserve the legacy portable single-run path on platforms that cannot
        # provide the stronger POSIX descriptor contract. Batch retrieval never
        # reaches this fallback: RootArtifactReader fails closed at its boundary.
        if not target.is_file():
            raise MillefeuilleContractError(f"{label} not found: {target}")
        return

    parent_fd, fd, name, opened_stat = _open_regular_file_fd(target, label=label)
    try:
        final_fd_stat = os.fstat(fd)
        final_named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected = _stable_stat_identity(opened_stat)
        if _stable_stat_identity(final_fd_stat) != expected:
            raise MillefeuilleContractError(
                f"{label} changed while being verified: {target}"
            )
        if _stable_stat_identity(final_named_stat) != expected:
            raise MillefeuilleContractError(
                f"{label} changed while being verified: {target}"
            )
    finally:
        os.close(fd)
        os.close(parent_fd)


def read_bytes_no_follow(path: str | Path, label: str) -> bytes:
    """Read stable bytes with no-follow descriptors when the platform supports it."""

    target = Path(path)
    if not _supports_no_follow():
        try:
            return target.read_bytes()
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not read {label} {target}: {exc}"
            ) from exc

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
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        final_fd_stat = os.fstat(fd)
        final_named_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        expected = _stable_stat_identity(opened_stat)
        if _stable_stat_identity(final_fd_stat) != expected:
            raise MillefeuilleContractError(f"{label} changed while read: {target}")
        if _stable_stat_identity(final_named_stat) != expected:
            raise MillefeuilleContractError(f"{label} changed while read: {target}")
        if len(payload) != opened_stat.st_size:
            raise MillefeuilleContractError(f"{label} changed while read: {target}")
        return payload
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {label} {target}: {exc}"
        ) from exc
    finally:
        os.close(fd)
        os.close(parent_fd)


def read_text_no_follow(path: str | Path, label: str) -> str:
    target = Path(path)
    try:
        return read_bytes_no_follow(target, label).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(
            f"{label} is not valid UTF-8: {target}"
        ) from exc


def load_json_object_no_follow(path: str | Path, label: str) -> dict[str, Any]:
    target = Path(path)
    try:
        payload = json.loads(read_text_no_follow(target, label))
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON: {target}") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return payload
