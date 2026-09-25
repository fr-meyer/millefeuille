"""Root-only, atomic filesystem commit for an already authorized GPT bundle.

The caller must replan the bundle and consume its exact one-use write receipt
before calling this primitive. This module makes no approval decision. Source
packs have a cooperative same-UID ancestor on GCP: descriptor-relative access
prevents symlink traversal, while a peer that can rename that ancestor can
still move the resulting tree. The caller must audit that risk separately.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import _open_directory_path_no_follow
from millefeuille.domain.summary_output_write_scope import SummaryOutputWritePreview
from millefeuille.domain.summary_publication_bundle import (
    PlannedPublicationFile,
    SummaryPublicationBundle,
    _canonical_json,
)

_ROOT_UID = 0
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


@dataclass(frozen=True)
class GptSummaryFilesystemCommit:
    run_id: str
    file_count: int
    total_bytes: int
    bundle_manifest_sha256: str
    source_pack_root: str


class GptSummaryPublicationError(MillefeuilleContractError):
    """Commit failure with the state needed for a conservative broker audit."""

    def __init__(self, *, committed: bool, cleanup_complete: bool) -> None:
        self.committed = committed
        self.cleanup_complete = cleanup_complete
        state = "uncertain after rename" if committed else "failed before rename"
        if not cleanup_complete:
            state += "; private-stage cleanup uncertain"
        super().__init__(f"GPT publication {state}")


def commit_prevalidated_gpt_summary_bundle(
    *, bundle: SummaryPublicationBundle, source_pack_root: str | Path
) -> GptSummaryFilesystemCommit:
    """Stage exact files privately, then atomically publish one new run tree.

    This is an internal root primitive. The trusted broker must reserve the
    receipt and revalidate source and output immediately before invoking it.
    Any error after the rename may leave a complete published run; callers
    must record an uncertain outcome and inspect it before any retry.
    """

    if sys.platform != "linux" or os.geteuid() != _ROOT_UID:
        raise MillefeuilleContractError("GPT publication requires Linux root")
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise MillefeuilleContractError("GPT publication requires no-follow support")
    root = Path(source_pack_root)
    if not root.is_absolute() or os.path.normpath(str(root)) != str(root):
        raise MillefeuilleContractError("GPT publication source-pack root is invalid")
    relative_files = _validate_bundle(bundle)
    root_fd = _open_directory_path_no_follow(root, label="GPT publication source pack")
    analyses_fd: int | None = None
    parent_fd: int | None = None
    stage_fd: int | None = None
    stage_name: str | None = None
    stage_created = False
    committed = False
    result: GptSummaryFilesystemCommit | None = None
    failure: Exception | None = None
    cleanup_complete = True
    try:
        analyses_fd = _open_or_create_child(root_fd, "analyses")
        parent_fd = _open_or_create_child(analyses_fd, "millefeuille", root_only=True)
        if _entry_exists(parent_fd, bundle.preview.run_id):
            raise MillefeuilleContractError("GPT publication run already exists")
        stage_name = ".gpt-summary-stage-" + uuid.uuid4().hex
        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        stage_created = True
        stage_fd = os.open(stage_name, _DIR_FLAGS, dir_fd=parent_fd)
        os.fchmod(stage_fd, 0o700)
        stage_stat = os.fstat(stage_fd)
        if stage_stat.st_uid != _ROOT_UID or not stat.S_ISDIR(stage_stat.st_mode):
            raise MillefeuilleContractError("GPT publication stage is not root-owned")
        _write_stage(stage_fd, relative_files)
        os.fchmod(stage_fd, 0o755)
        os.fsync(stage_fd)
        _require_same_path(root, root_fd)
        _require_same_path(root / "analyses", analyses_fd)
        _require_same_path(root / "analyses" / "millefeuille", parent_fd)
        _require_stage_identity(parent_fd, stage_name, stage_stat)
        _rename_noreplace(parent_fd, stage_name, bundle.preview.run_id)
        committed = True
        os.fsync(parent_fd)
        _require_same_path(
            root / "analyses" / "millefeuille" / bundle.preview.run_id,
            stage_fd,
        )
        result = GptSummaryFilesystemCommit(
            run_id=bundle.preview.run_id,
            file_count=len(relative_files),
            total_bytes=bundle.total_bytes,
            bundle_manifest_sha256=bundle.bundle_manifest_sha256,
            source_pack_root=str(root),
        )
    except Exception as exc:
        failure = exc
    finally:
        if (
            not committed
            and parent_fd is not None
            and stage_name is not None
            and stage_created
        ):
            cleanup_complete = _clean_owned_stage(parent_fd, stage_name, stage_fd)
        for fd in (stage_fd, parent_fd, analyses_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as exc:
                    if failure is None:
                        failure = exc
    if failure is not None:
        raise GptSummaryPublicationError(
            committed=committed, cleanup_complete=cleanup_complete
        ) from failure
    assert result is not None
    return result


def _validate_bundle(
    bundle: SummaryPublicationBundle,
) -> tuple[tuple[tuple[str, ...], bytes], ...]:
    if not isinstance(bundle, SummaryPublicationBundle):
        raise MillefeuilleContractError("GPT publication bundle is invalid")
    preview = bundle.preview
    if not isinstance(preview, SummaryOutputWritePreview):
        raise MillefeuilleContractError("GPT publication preview is invalid")
    if not isinstance(preview.run_id, str) or not _RUN_ID.fullmatch(preview.run_id):
        raise MillefeuilleContractError("GPT publication run id is invalid")
    prefix = ("analyses", "millefeuille", preview.run_id)
    if (
        not isinstance(bundle.files, tuple)
        or not bundle.files
        or any(not isinstance(item, PlannedPublicationFile) for item in bundle.files)
        or any(
            not isinstance(item.data, bytes)
            or not isinstance(item.ref, str)
            or not isinstance(item.sha256, str)
            for item in bundle.files
        )
        or not isinstance(bundle.bundle_manifest_json, bytes)
        or not isinstance(bundle.bundle_manifest_sha256, str)
        or not isinstance(bundle.total_bytes, int)
        or tuple(item.ref for item in bundle.files) != preview.file_refs
        or bundle.total_bytes != sum(len(item.data) for item in bundle.files)
        or not _DIGEST.fullmatch(bundle.bundle_manifest_sha256)
        or "sha256:" + hashlib.sha256(bundle.bundle_manifest_json).hexdigest()
        != bundle.bundle_manifest_sha256
    ):
        raise MillefeuilleContractError("GPT publication bundle identity drift")
    try:
        manifest = json.loads(bundle.bundle_manifest_json)
    except (TypeError, ValueError, RecursionError) as exc:
        raise MillefeuilleContractError("GPT publication manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version")
        != "millefeuille-gpt-summary-publication-bundle/v0.1"
        or manifest.get("paper_id") != preview.paper_id
        or manifest.get("run_id") != preview.run_id
        or manifest.get("packet_digest") != preview.packet_digest
        or manifest.get("receipt_digest") != preview.receipt_digest
        or manifest.get("source_manifest_sha256") != preview.source_manifest_sha256
        or manifest.get("write_manifest_sha256") != preview.write_manifest_sha256
        or manifest.get("observed_usage_sha256") != preview.observed_usage_sha256
        or manifest.get("provenance_manifest_sha256")
        != preview.provenance_manifest_sha256
        or manifest.get("entries")
        != [
            {"ref": item.ref, "sha256": item.sha256, "bytes": len(item.data)}
            for item in bundle.files
        ]
        or _canonical_json(manifest) != bundle.bundle_manifest_json
    ):
        raise MillefeuilleContractError("GPT publication manifest identity drift")
    result: list[tuple[tuple[str, ...], bytes]] = []
    seen: set[tuple[str, ...]] = set()
    for item in bundle.files:
        parts = tuple(item.ref.split("/"))
        relative = parts[len(prefix) :]
        if (
            parts[: len(prefix)] != prefix
            or not relative
            or any(part in {"", ".", ".."} for part in relative)
            or any("\x00" in part or "\\" in part for part in relative)
            or relative in seen
            or not _DIGEST.fullmatch(item.sha256)
            or "sha256:" + hashlib.sha256(item.data).hexdigest() != item.sha256
        ):
            raise MillefeuilleContractError("GPT publication file scope drift")
        seen.add(relative)
        result.append((relative, item.data))
    return tuple(result)


def _open_or_create_child(parent_fd: int, name: str, *, root_only: bool = False) -> int:
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        created = False
        try:
            os.mkdir(name, mode=0o755, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            created = False
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        if created:
            if os.fstat(fd).st_uid != _ROOT_UID:
                os.close(fd)
                raise MillefeuilleContractError(
                    "GPT publication newly created directory changed"
                ) from None
            os.fchmod(fd, 0o755)
            os.fsync(parent_fd)
    if root_only:
        info = os.fstat(fd)
        if info.st_uid != _ROOT_UID or stat.S_IMODE(info.st_mode) != 0o755:
            os.close(fd)
            raise MillefeuilleContractError(
                "GPT publication parent must be root-owned and readable by the app"
            )
    return fd


def _write_stage(
    stage_fd: int, files: tuple[tuple[tuple[str, ...], bytes], ...]
) -> None:
    directories: dict[tuple[str, ...], int] = {(): stage_fd}
    try:
        for parts, data in files:
            for depth in range(1, len(parts)):
                key = parts[:depth]
                if key not in directories:
                    directories[key] = _open_or_create_child(
                        directories[key[:-1]], key[-1]
                    )
            parent_fd = directories[parts[:-1]]
            fd = os.open(parts[-1], _FILE_FLAGS, 0o644, dir_fd=parent_fd)
            try:
                if os.fstat(fd).st_uid != _ROOT_UID:
                    raise MillefeuilleContractError(
                        "GPT publication file is not root-owned"
                    )
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                os.fsync(fd)
            finally:
                os.close(fd)
        for key in sorted(directories, key=len, reverse=True):
            os.fsync(directories[key])
    finally:
        for key, fd in directories.items():
            if key:
                os.close(fd)


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _require_same_path(path: Path, held_fd: int) -> None:
    fresh_fd = _open_directory_path_no_follow(path, label="GPT publication path")
    try:
        held = os.fstat(held_fd)
        fresh = os.fstat(fresh_fd)
        if (held.st_dev, held.st_ino) != (fresh.st_dev, fresh.st_ino):
            raise MillefeuilleContractError("GPT publication path changed")
    finally:
        os.close(fresh_fd)


def _require_stage_identity(parent_fd: int, name: str, held: os.stat_result) -> None:
    fresh = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (fresh.st_dev, fresh.st_ino) != (held.st_dev, held.st_ino):
        raise MillefeuilleContractError("GPT publication stage changed")


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise MillefeuilleContractError(
            "GPT publication requires Linux atomic no-replace rename"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1
    ):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise MillefeuilleContractError("GPT publication run already exists")
        raise OSError(error, os.strerror(error))


def _clean_owned_stage(parent_fd: int, name: str, stage_fd: int | None) -> bool:
    try:
        if stage_fd is not None:
            _require_stage_identity(parent_fd, name, os.fstat(stage_fd))
        elif os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_uid != _ROOT_UID:
            return False
        shutil.rmtree(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except FileNotFoundError:
        return True
    except (OSError, MillefeuilleContractError):
        return False
