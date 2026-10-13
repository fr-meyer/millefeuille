"""Recoverable metadata transactions for cooperating native readers/writers.

Per-entry rename is atomic; a group is not a filesystem-wide atomic snapshot.
Native readers share the run lock and reject a pending journal. Uncooperative
name changes preserve evidence and require recovery rather than blind rollback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from millefeuille.domain import secure_io
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import (
    _open_directory_path_no_follow,
    _open_or_create_directory_path_no_follow,
    _open_regular_file_fd,
    _open_relative_directory_fd,
    _portable_lstat_regular_file,
    _read_opened_file_bytes,
    _require_portable_directory,
    _stat_stability_snapshot,
    read_bytes_no_follow,
    write_new_text_no_follow,
)

PENDING = ".millefeuille-metadata-pending"
HISTORY = ".millefeuille-metadata-history"
_MAX_FILE_BYTES = 16 * 1024 * 1024
_HAS_METADATA_DIR_FD = (
    all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.stat, os.unlink)
    )
    and os.stat in os.supports_follow_symlinks
)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _binding(state: dict | None) -> dict | None:
    if state is None:
        return None
    return {key: state[key] for key in ("inode", "bytes", "sha256")}


def _state(parent_fd: int, name: str) -> dict | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MillefeuilleContractError("metadata entry is unsafe") from error
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise MillefeuilleContractError("metadata is not a single-link file")
        payload = _read_opened_file_bytes(
            fd, before, path=Path(name), label="metadata", max_bytes=_MAX_FILE_BYTES
        )
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(before) != _identity(named)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or after.st_nlink != 1
            or named.st_nlink != 1
        ):
            raise MillefeuilleContractError("metadata changed while read")
        return {
            "inode": list(_identity(after)),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "data": payload,
        }
    finally:
        os.close(fd)


def capture_metadata_binding(path: str | Path) -> dict | None:
    target = Path(path)
    if os.name != "posix" or not secure_io._supports_no_follow():
        if ".." in target.parts:
            raise MillefeuilleContractError("metadata path contains parent traversal")
        for parent in target.parents:
            _require_portable_directory(parent, target=target, label="metadata")
        try:
            target.lstat()
        except FileNotFoundError:
            return None
        before = _portable_lstat_regular_file(target, label="metadata")
        if before.st_nlink != 1:
            raise MillefeuilleContractError("metadata is not a single-link file")
        data = read_bytes_no_follow(target, "metadata", max_bytes=_MAX_FILE_BYTES)
        after = _portable_lstat_regular_file(target, label="metadata")
        if _identity(before) != _identity(after) or _stat_stability_snapshot(
            before
        ) != _stat_stability_snapshot(after):
            raise MillefeuilleContractError("portable metadata changed")
        return {
            "inode": list(_identity(after)),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    fd = _open_directory_path_no_follow(target.parent, label="metadata parent")
    try:
        return _binding(_state(fd, target.name))
    finally:
        os.close(fd)


def _pending(directory_fd: int) -> None:
    try:
        os.stat(PENDING, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise MillefeuilleContractError("metadata transaction pending; recovery required")


@contextmanager
def metadata_read_guard(directory: str | Path) -> Iterator[tuple[int, int]]:
    path = Path(directory)
    if os.name != "posix" or not secure_io._supports_no_follow():
        if ".." in path.parts:
            raise MillefeuilleContractError("metadata path contains parent traversal")
        ancestors = []
        for parent in (path, *path.parents):
            _require_portable_directory(parent, target=path, label="metadata")
            identity = _identity(parent.lstat())
            if not identity[1]:
                raise MillefeuilleContractError(
                    "portable metadata identity unavailable"
                )
            ancestors.append((parent, identity))
        identity = ancestors[0][1]

        def portable_pending() -> None:
            try:
                (path / PENDING).lstat()
            except FileNotFoundError:
                return
            raise MillefeuilleContractError(
                "metadata transaction pending; recovery required"
            )

        portable_pending()
        yield identity
        portable_pending()
        if _identity(path.lstat()) != identity:
            raise MillefeuilleContractError("portable metadata directory changed")
        for parent, before_identity in ancestors:
            _require_portable_directory(parent, target=path, label="metadata")
            if _identity(parent.lstat()) != before_identity:
                raise MillefeuilleContractError("portable metadata ancestor changed")
        return
    try:
        import fcntl
    except ImportError as error:
        raise MillefeuilleContractError(
            "metadata read requires POSIX flock support"
        ) from error

    fd = _open_directory_path_no_follow(path, label="metadata read directory")
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        identity = _identity(os.fstat(fd))
        _pending(fd)
        yield identity
        _pending(fd)
        rebound = _open_directory_path_no_follow(path, label="metadata directory")
        try:
            if _identity(os.fstat(rebound)) != identity:
                raise MillefeuilleContractError("metadata directory changed")
        finally:
            os.close(rebound)
    finally:
        os.close(fd)


def _metadata_directory(path: str | Path) -> Path | None:
    target = Path(path)
    for directory in (target.parent, *target.parent.parents):
        marked = False
        for name in (PENDING, HISTORY):
            try:
                (directory / name).lstat()
            except FileNotFoundError:
                continue
            marked = True
        pair = all(
            (directory / name).exists()
            for name in ("stage-manifest.json", "artifact-index.json")
        )
        if marked or pair:
            return directory
    return None


@contextmanager
def metadata_path_read_guard(path: str | Path) -> Iterator[None]:
    directory = _metadata_directory(path)
    if directory is not None:
        with metadata_read_guard(directory):
            yield
        return
    yield


def _rename_entry(
    source_fd: int,
    source: str,
    destination_fd: int,
    destination: str,
    *,
    exchange: bool = False,
) -> None:
    """Use the repository's Linux/macOS descriptor-relative rename convention."""
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = library.renameatx_np
            flags = 2 if exchange else 4
        else:
            rename = library.renameat2
            flags = 2 if exchange else 1
    except AttributeError as error:
        raise MillefeuilleContractError("atomic metadata rename unavailable") from error
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    if (
        rename(
            source_fd,
            os.fsencode(source),
            destination_fd,
            os.fsencode(destination),
            flags,
        )
        != 0
    ):
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FileExistsError(destination)
        raise OSError(number, os.strerror(number), destination)


def _child(parent_fd: int, name: str) -> int:
    try:
        return _open_relative_directory_fd(parent_fd, name)
    except FileNotFoundError:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return _open_relative_directory_fd(parent_fd, name)


def _output_parent(root_fd: int, relative: Path) -> int:
    fd = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            following = _child(fd, part)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def require_metadata_write_support() -> None:
    """Reject an unqualified native writer before creating directories or outputs."""
    if os.name != "posix":
        raise MillefeuilleContractError(
            "metadata writes require POSIX transaction support"
        )
    try:
        import fcntl  # noqa: F401
    except (ImportError, OSError) as error:
        raise MillefeuilleContractError(
            "metadata writes require POSIX transaction support"
        ) from error
    if (
        not secure_io._supports_no_follow()
        or not _HAS_METADATA_DIR_FD
        or not hasattr(os, "fchmod")
        or not hasattr(os, "fsync")
    ):
        raise MillefeuilleContractError(
            "metadata writes require descriptor-relative no-follow support"
        )
    symbol = "renameatx_np" if sys.platform == "darwin" else "renameat2"
    try:
        getattr(ctypes.CDLL(None, use_errno=True), symbol)
    except (AttributeError, OSError) as error:
        raise MillefeuilleContractError("atomic metadata rename unavailable") from error


def write_metadata_transaction(
    directory: str | Path,
    outputs: dict[str, str],
    *,
    expected_before: dict[str, dict | None] | None = None,
    expected_directory_identity: tuple[int, int] | None = None,
    check_inputs: Callable[[], None] | None = None,
) -> None:
    """Publish one guarded group, preserving exact before bytes and failed state.

    Successful return requires exact readback, input checks, directory durability,
    and removal of the pending barrier under the exclusive run-inode lock. Failure
    rolls back only exact owned entries. Failed rollback leaves readers blocked.
    All journals remain available for separately authorized recovery.
    """
    require_metadata_write_support()
    import fcntl

    if not outputs or len(outputs) > 16:
        raise MillefeuilleContractError("metadata output set is invalid")
    relatives = [Path(name) for name in outputs]
    if any(
        p.is_absolute()
        or ".." in p.parts
        or not p.parts
        or p.parts[0].startswith(".millefeuille-metadata-")
        for p in relatives
    ):
        raise MillefeuilleContractError("metadata output path is unsafe")
    root = Path(directory).absolute()
    root_fd = _open_or_create_directory_path_no_follow(root, label="metadata root")
    parents: list[int] = []
    journal_fd = history_fd = None
    entries: list[dict] = []
    archived = False
    archive_name = ""
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        root_identity = _identity(os.fstat(root_fd))
        if (
            expected_directory_identity is not None
            and root_identity != expected_directory_identity
        ):
            raise MillefeuilleContractError("metadata run directory drift")
        _pending(root_fd)

        def check_directory() -> None:
            rebound = _open_directory_path_no_follow(root, label="metadata root")
            try:
                if _identity(os.fstat(rebound)) != root_identity:
                    raise MillefeuilleContractError("metadata run directory drift")
            finally:
                os.close(rebound)
            if journal_fd is not None:
                observed = os.stat(
                    archive_name if archived else PENDING,
                    dir_fd=history_fd if archived else root_fd,
                    follow_symlinks=False,
                )
                if _identity(observed) != _identity(os.fstat(journal_fd)):
                    raise MillefeuilleContractError("metadata journal directory drift")
            for entry in entries:
                rebound = _open_directory_path_no_follow(
                    root / entry["relative"].parent, label="metadata output parent"
                )
                try:
                    if _identity(os.fstat(rebound)) != entry["parent_inode"]:
                        raise MillefeuilleContractError("metadata output parent drift")
                finally:
                    os.close(rebound)
            if check_inputs is not None:
                check_inputs()

        for number, (name, text) in enumerate(outputs.items()):
            relative = Path(name)
            parent_fd = _output_parent(root_fd, relative)
            parents.append(parent_fd)
            before = _state(parent_fd, relative.name)
            if (expected_before is not None and name in expected_before) and _binding(
                before
            ) != expected_before[name]:
                raise MillefeuilleContractError("metadata expected-before drift")
            data = text.encode("utf-8")
            if len(data) > _MAX_FILE_BYTES:
                raise MillefeuilleContractError("metadata output exceeds limit")
            entries.append(
                {
                    "relative": relative,
                    "parent_fd": parent_fd,
                    "parent_inode": _identity(os.fstat(parent_fd)),
                    "before": before,
                    "data": data,
                    "number": number,
                    "attempted": False,
                    "installed": False,
                }
            )
        check_directory()
        if all(
            e["before"] is not None and e["before"]["data"] == e["data"]
            for e in entries
        ):
            return
        packet = [
            {
                "path": str(e["relative"]),
                "before": _binding(e["before"]),
                "after": {
                    "bytes": len(e["data"]),
                    "sha256": hashlib.sha256(e["data"]).hexdigest(),
                },
            }
            for e in entries
        ]
        encoded = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        archive_name = hashlib.sha256(encoded).hexdigest()
        history_fd = _child(root_fd, HISTORY)
        try:
            os.stat(archive_name, dir_fd=history_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise MillefeuilleContractError("metadata recovery journal collision")
        os.mkdir(PENDING, 0o700, dir_fd=root_fd)
        journal_fd = _open_relative_directory_fd(root_fd, PENDING)
        journal_identity = _identity(os.fstat(journal_fd))
        os.fsync(root_fd)
        for entry in entries:
            number = entry["number"]
            if entry["before"] is not None:
                write_new_text_no_follow(
                    root / PENDING / f"before-{number}.txt",
                    entry["before"]["data"].decode("utf-8"),
                    "metadata backup",
                    expected_parent_identity=journal_identity,
                    preserve_created_on_failure=True,
                )
                if (
                    _state(journal_fd, f"before-{number}.txt")["data"]
                    != entry["before"]["data"]
                ):
                    raise MillefeuilleContractError("metadata exact backup drift")
            write_new_text_no_follow(
                root / PENDING / f"next-{number}.txt",
                entry["data"].decode("utf-8"),
                "staged metadata",
                expected_parent_identity=journal_identity,
                preserve_created_on_failure=True,
            )
            entry["after"] = _state(journal_fd, f"next-{number}.txt")
            if entry["after"]["data"] != entry["data"]:
                raise MillefeuilleContractError("metadata staged-byte drift")
        write_new_text_no_follow(
            root / PENDING / "plan.json",
            encoded.decode() + "\n",
            "metadata transaction plan",
            preserve_created_on_failure=True,
            expected_parent_identity=journal_identity,
        )
        check_directory()
        for entry in entries:
            current = _state(entry["parent_fd"], entry["relative"].name)
            if _binding(current) != _binding(entry["before"]):
                raise MillefeuilleContractError("metadata changed before commit")
            entry["attempted"] = True
            _rename_entry(
                journal_fd,
                f"next-{entry['number']}.txt",
                entry["parent_fd"],
                entry["relative"].name,
                exchange=entry["before"] is not None,
            )
            entry["installed"] = True
            if _binding(_state(entry["parent_fd"], entry["relative"].name)) != _binding(
                entry["after"]
            ):
                raise MillefeuilleContractError("metadata installed-output drift")
            if entry["before"] is not None and _binding(
                _state(journal_fd, f"next-{entry['number']}.txt")
            ) != _binding(entry["before"]):
                raise MillefeuilleContractError("metadata displaced-entry drift")
            os.fsync(entry["parent_fd"])
            os.fsync(journal_fd)
            check_directory()
        for entry in entries:
            if _binding(_state(entry["parent_fd"], entry["relative"].name)) != _binding(
                entry["after"]
            ):
                raise MillefeuilleContractError("metadata final-output drift")
        os.fsync(journal_fd)
        check_directory()
        _rename_entry(root_fd, PENDING, history_fd, archive_name)
        archived = True
        if (
            _identity(os.stat(archive_name, dir_fd=history_fd, follow_symlinks=False))
            != journal_identity
        ):
            raise MillefeuilleContractError("metadata journal name drift")
        os.fsync(history_fd)
        os.fsync(root_fd)
        check_directory()
    except Exception as cause:
        if journal_fd is not None:
            rollback_errors = []
            if archived:
                try:
                    _rename_entry(history_fd, archive_name, root_fd, PENDING)
                    archived = False
                    os.fsync(root_fd)
                except Exception as error:
                    rollback_errors.append(type(error).__name__)
            for entry in reversed(entries):
                if not entry.get("attempted"):
                    continue
                try:
                    parent_fd = entry["parent_fd"]
                    name = entry["relative"].name
                    current = _state(parent_fd, name)
                    if _binding(current) == _binding(entry["before"]):
                        continue
                    if _binding(current) != _binding(entry.get("after")):
                        raise MillefeuilleContractError(
                            "rollback output ownership drift"
                        )
                    if entry["before"] is not None:
                        if _binding(
                            _state(journal_fd, f"next-{entry['number']}.txt")
                        ) != _binding(entry["before"]):
                            raise MillefeuilleContractError(
                                "rollback backup ownership drift"
                            )
                        _rename_entry(
                            journal_fd,
                            f"next-{entry['number']}.txt",
                            parent_fd,
                            name,
                            exchange=True,
                        )
                    else:
                        _rename_entry(
                            parent_fd, name, journal_fd, f"failed-{entry['number']}.txt"
                        )
                    returned = (
                        f"next-{entry['number']}.txt"
                        if entry["before"] is not None
                        else f"failed-{entry['number']}.txt"
                    )
                    if _binding(_state(journal_fd, returned)) != _binding(
                        entry["after"]
                    ):
                        raise MillefeuilleContractError(
                            "rollback displaced-output drift"
                        )
                    if _binding(_state(parent_fd, name)) != _binding(entry["before"]):
                        raise MillefeuilleContractError("rollback result drift")
                    os.fsync(parent_fd)
                except Exception as error:
                    rollback_errors.append(type(error).__name__)
            try:
                check_directory()
                if not rollback_errors and not archived:
                    for entry in entries:
                        if _binding(
                            _state(entry["parent_fd"], entry["relative"].name)
                        ) != _binding(entry["before"]):
                            raise MillefeuilleContractError("rollback pre-state drift")
                    os.fsync(journal_fd)
                    _rename_entry(
                        root_fd, PENDING, history_fd, archive_name + "-rolled-back"
                    )
                    os.fsync(history_fd)
                    os.fsync(root_fd)
            except Exception as error:
                rollback_errors.append(type(error).__name__)
            if rollback_errors:
                # A preserved barrier also covers a commit/rollback fsync failure
                # after the journal moved. Never overwrite an existing marker.
                try:
                    os.mkdir(PENDING, 0o700, dir_fd=root_fd)
                    os.fsync(root_fd)
                except FileExistsError:
                    pass
                except OSError:
                    rollback_errors.append("pending-marker-durability-uncertain")
            if "pending-marker-durability-uncertain" in rollback_errors:
                suffix = (
                    "recovery required; journal retained; "
                    "pending barrier durability uncertain"
                )
            elif rollback_errors:
                suffix = "recovery required; pending barrier retained"
            else:
                suffix = "rolled back; journal retained"
            raise MillefeuilleContractError(
                "metadata transaction failed: " + suffix
            ) from cause
        if isinstance(cause, OSError):
            raise MillefeuilleContractError(
                "metadata transaction preflight failed"
            ) from cause
        raise
    finally:
        for fd in parents:
            os.close(fd)
        if journal_fd is not None:
            os.close(journal_fd)
        if history_fd is not None:
            os.close(history_fd)
        os.close(root_fd)


def write_metadata_text(path: str | Path, text: str) -> None:
    target = Path(path)
    directory = _metadata_directory(target) or target.parent
    write_metadata_transaction(directory, {str(target.relative_to(directory)): text})


def capture_input_files(paths: list[Path]) -> Callable[[], None]:
    """Capture bounded exact bytes/inodes; caller supplies already validated refs."""

    def capture() -> list[tuple[str, dict | None]]:
        result = []
        total = 0
        for path in sorted(set(paths)):
            parent_fd, fd, name, before_stat = _open_regular_file_fd(
                path, label="transaction input"
            )
            try:
                data = _read_opened_file_bytes(
                    fd,
                    before_stat,
                    path=path,
                    label="transaction input",
                    max_bytes=64 * 1024 * 1024,
                )
                after_stat = os.fstat(fd)
                named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if _identity(named) != _identity(
                    before_stat
                ) or _stat_stability_snapshot(after_stat) != _stat_stability_snapshot(
                    before_stat
                ):
                    raise MillefeuilleContractError(
                        "transaction input changed while read"
                    )
            finally:
                os.close(fd)
                os.close(parent_fd)
            total += len(data)
            if len(result) >= 4096 or total > 512 * 1024 * 1024:
                raise MillefeuilleContractError("transaction input inventory limit")
            result.append(
                (
                    str(path),
                    {
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "inode": list(_identity(before_stat)),
                    },
                )
            )
        return result

    before = capture()

    def check() -> None:
        if capture() != before:
            raise MillefeuilleContractError("transaction input drift")

    return check


def _origin_inventory_run(source_pack_root: Path, tree: Path) -> Path:
    """Project only the two validated original-run inventory layouts."""
    base = source_pack_root / "analyses" / "millefeuille"
    try:
        parts = tree.relative_to(base).parts
    except ValueError as error:
        raise MillefeuilleContractError(
            "transaction origin inventory root drift"
        ) from error
    if any(part in {".", ".."} for part in parts):
        raise MillefeuilleContractError("transaction origin inventory layout drift")
    if len(parts) == 1:
        return tree
    if len(parts) == 2 and parts[1] == "summaries":
        return tree.parent
    raise MillefeuilleContractError("transaction origin inventory layout drift")


def capture_run_inputs(
    source_pack_root: Path,
    source_pack_dir: Path,
    run_dir: Path,
    extra_inputs: list[Path],
) -> Callable[[], None]:
    """Bind source assets, selected/origin run trees and explicit evidence."""
    from millefeuille.domain.multi_source_run_package import (
        _published_summary_inventory_paths,
    )

    def discover() -> tuple[list[Path], list[tuple[str, tuple[int, int]]]]:
        if (run_dir / "summaries/hierarchical-summary.json").exists():
            origin_trees, evidence = _published_summary_inventory_paths(
                source_pack_root, run_dir
            )
        else:
            origin_trees, evidence = (), ()
        # D77 returns the summaries directory; the separately reviewed inventory
        # repair returns the complete origin run. Preserve exactly that run.
        origins = [
            _origin_inventory_run(source_pack_root, tree) for tree in origin_trees
        ]
        trees = [
            source_pack_dir,
            run_dir / "summaries",
            run_dir / "cards",
            run_dir / "index",
            *origins,
        ]
        files = set(extra_inputs) | set(evidence)
        directories = []
        pending = [(tree, 0) for tree in trees]
        seen = set()
        nodes = 0
        while pending:
            path, depth = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            nodes += 1
            if nodes > 8192 or depth > 16:
                raise MillefeuilleContractError("transaction traversal limit")
            if not path.exists():
                directories.append((str(path), (0, 0)))
                continue
            fd = _open_directory_path_no_follow(path, label="transaction input tree")
            try:
                directories.append((str(path), _identity(os.fstat(fd))))
                for name in sorted(os.listdir(fd)):
                    # Canonical run packages are bound by the explicit trees.
                    # Source assets exclude this separate run namespace.
                    if path == source_pack_dir / "analyses" and name == "millefeuille":
                        continue
                    child = path / name
                    if name in {PENDING, HISTORY}:
                        continue
                    if path == run_dir and name in {
                        "stage-manifest.json",
                        "artifact-index.json",
                        "reports",
                    }:
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    nodes += 1
                    if nodes > 8192:
                        raise MillefeuilleContractError("transaction traversal limit")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append((child, depth + 1))
                    elif stat.S_ISREG(info.st_mode):
                        files.add(child)
                    else:
                        raise MillefeuilleContractError("unsafe transaction input")
            finally:
                os.close(fd)
        return sorted(files), sorted(directories)

    paths, directories = discover()
    check_files = capture_input_files(paths)

    def check() -> None:
        if discover() != (paths, directories):
            raise MillefeuilleContractError("transaction input tree drift")
        check_files()

    return check
