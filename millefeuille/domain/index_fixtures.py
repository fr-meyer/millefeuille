"""Fixture-first retrieval/index helpers for verified source packs."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import copy
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

from millefeuille.domain.card_fixtures import CARD_JSON_REF
from millefeuille.domain.card_index_contract import (
    LoadedJsonArtifact,
    canonical_json_bytes,
    load_and_validate_canonical_card_index,
    load_paper_card_artifact,
    load_retrieval_index_artifact,
    observed_index_state,
    validate_canonical_card_index_contract,
    validate_paper_card_identity,
)
from millefeuille.domain.card_index_contract import (
    validate_observed_card_index_state as _validate_observed_card_index_state,
)
from millefeuille.domain.millefeuille import (
    PAPER_CARD_SCHEMA_V1,
    PAPER_CARD_SCHEMA_V2,
    MillefeuilleContractError,
    PaperCardRecord,
    RetrievalIndexRecord,
)
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    load_route_selection_sidecar,
)
from millefeuille.domain.secure_io import load_json_object_no_follow
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)

INDEX_FIXTURE_SCHEMA_VERSION = "millefeuille-index-fixture-evidence/v0.1"
RETRIEVAL_INDEX_STATUS_SCHEMA_VERSION = "millefeuille-retrieval-index-status/v0.1"
INDEX_STATUS_REF = Path("index/index-status.json")
CARD_INDEX_TRANSACTION_ROOT_REF = Path("recovery/card-index")
CARD_INDEX_TRANSACTION_MAX_CARD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class IndexFixtureEvidence:
    item_key: str
    attachment_key: str
    canonical_filename: str
    index_status_path: Path
    expected_sha256: str
    source_type: str = "zotero"
    schema_version: str = INDEX_FIXTURE_SCHEMA_VERSION
    zotero_version: int | None = None
    paper_id: str | None = None

    def __post_init__(self) -> None:
        if self.source_type != "zotero":
            raise MillefeuilleContractError("source_type must be 'zotero'")
        object.__setattr__(self, "index_status_path", Path(self.index_status_path))
        object.__setattr__(
            self,
            "expected_sha256",
            _normalize_sha256(self.expected_sha256),
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_dir: str | Path | None = None,
    ) -> IndexFixtureEvidence:
        if not isinstance(payload, dict):
            raise MillefeuilleContractError("index fixture evidence must be an object")
        schema_version = _required_string(
            payload.get("schema_version", INDEX_FIXTURE_SCHEMA_VERSION),
            "schema_version",
        )
        if schema_version != INDEX_FIXTURE_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                f"unsupported index fixture evidence schema_version {schema_version!r}"
            )
        index_status_path = Path(
            _required_string(
                payload.get("index_status_path") or payload.get("index_path"),
                "index_status_path",
            )
        )
        if not index_status_path.is_absolute() and base_dir is not None:
            index_status_path = Path(base_dir) / index_status_path
        return cls(
            item_key=_required_string(payload.get("item_key"), "item_key"),
            attachment_key=_required_string(
                payload.get("attachment_key"),
                "attachment_key",
            ),
            canonical_filename=_required_string(
                payload.get("canonical_filename"),
                "canonical_filename",
            ),
            index_status_path=index_status_path,
            expected_sha256=_required_string(
                payload.get("expected_sha256") or payload.get("sha256"),
                "expected_sha256",
            ),
            source_type=_required_string(
                payload.get("source_type", "zotero"),
                "source_type",
            ),
            schema_version=schema_version,
            zotero_version=_optional_int(
                payload.get("zotero_version"),
                "zotero_version",
            ),
            paper_id=_optional_string(payload.get("paper_id"), "paper_id"),
        )


@dataclass(frozen=True)
class IndexFixtureWriteResult:
    paper_id: str
    run_id: str
    status: str
    run_dir: Path
    index_status_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "run_dir": str(self.run_dir),
            "index_status_path": str(self.index_status_path),
        }


@dataclass(frozen=True)
class _PlannedIndexWrite:
    paper_id: str
    run_id: str
    status: str
    source_pack_dir: Path
    run_dir: Path
    index_status_output_path: Path
    expected_payload: dict[str, Any]
    card_json_path: Path
    original_card_payload: dict[str, Any]
    original_card_bytes: bytes
    refreshed_card_payload: dict[str, Any] | None


@dataclass(frozen=True)
class _DisplacedCardEntry:
    path: Path
    mechanism: str
    rejected_path: Path


@dataclass(frozen=True)
class _CapturedFileEntry:
    path: Path
    raw_bytes: bytes
    identity: tuple[int, ...]
    file_key: tuple[int, int]


def load_index_fixture_evidence_batch(path: str | Path) -> list[IndexFixtureEvidence]:
    evidence_path = Path(path)
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read index fixture evidence {evidence_path}: {exc}"
        ) from exc

    if evidence_path.suffix == ".jsonl":
        records: list[IndexFixtureEvidence] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MillefeuilleContractError(
                    "index fixture evidence JSONL is not valid JSON at "
                    f"{evidence_path}:{line_number}"
                ) from exc
            records.append(
                IndexFixtureEvidence.from_dict(
                    payload,
                    base_dir=evidence_path.parent,
                )
            )
        return records

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(
            f"index fixture evidence is not valid JSON: {evidence_path}"
        ) from exc
    if isinstance(payload, list):
        raw_records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("evidence"), list):
        raw_records = payload["evidence"]
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        raw_records = payload["records"]
    else:
        raw_records = [payload]
    return [
        IndexFixtureEvidence.from_dict(record, base_dir=evidence_path.parent)
        for record in raw_records
    ]


def write_indexes_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    artifact_run_dir: str | Path | None = None,
) -> list[IndexFixtureWriteResult]:
    root = Path(source_pack_root)
    resolved_run_id = _required_string(run_id, "run_id")
    records = load_index_fixture_evidence_batch(evidence_path)
    _reject_duplicate_records(records)
    planned = [
        _plan_index(
            record,
            root,
            resolved_run_id,
            artifact_run_dir=artifact_run_dir,
        )
        for record in records
    ]
    return [_apply_planned_index_write(plan) for plan in planned]


def load_retrieval_index_status(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, "retrieval index status")
    record = RetrievalIndexRecord.from_dict(payload)
    return record.to_dict()


def _plan_index(
    evidence: IndexFixtureEvidence,
    source_pack_root: Path,
    run_id: str,
    *,
    artifact_run_dir: str | Path | None,
) -> _PlannedIndexWrite:
    source_pack_dir, source_hash = _resolve_source_pack_dir(
        source_pack_root=source_pack_root,
        item_key=evidence.item_key,
        attachment_key=evidence.attachment_key,
        canonical_filename=evidence.canonical_filename,
        expected_sha256=evidence.expected_sha256,
        zotero_version=evidence.zotero_version,
        paper_id=evidence.paper_id,
    )
    paper_id = source_pack_dir.name
    run_dir = (
        Path(artifact_run_dir)
        if artifact_run_dir is not None
        else source_pack_dir / "analyses" / "millefeuille" / run_id
    )
    _validate_route_dependency(source_pack_dir=source_pack_dir, source_hash=source_hash)
    _validate_summary_dependency(
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
    )
    card_artifact = _validate_card_dependency(
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
    )
    fixture_payload = load_retrieval_index_status(evidence.index_status_path)
    index_status_output_path = run_dir / INDEX_STATUS_REF
    expected_payload = _materialize_index_payload(
        fixture_payload=fixture_payload,
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
        index_dir=index_status_output_path.parent,
    )
    refreshed_card_payload = _plan_card_index_refresh(
        card_payload=card_artifact.payload,
        index_payload=expected_payload,
    )
    status = _existing_index_status(
        index_status_output_path=index_status_output_path,
        expected_payload=expected_payload,
    )
    if card_artifact.payload["schema_version"] == PAPER_CARD_SCHEMA_V2:
        card_state = card_artifact.payload["index_state"]
        if card_state["phase"] == "observed":
            if status != "existing":
                raise MillefeuilleContractError(
                    "observed paper card requires its canonical retrieval index status"
                )
            validate_canonical_card_index_contract(
                card_payload=card_artifact.payload,
                index_payload=expected_payload,
                paper_id=paper_id,
                run_id=run_id,
                source_hash=source_hash,
                index_dir=index_status_output_path.parent,
                selected_fulltext_path=source_pack_dir / ROUTE_MARKDOWN_REF,
                summary_path=run_dir / SUMMARY_ARTIFACT_REF,
                card_path=run_dir / CARD_JSON_REF,
            )
    return _PlannedIndexWrite(
        paper_id=paper_id,
        run_id=run_id,
        status=status or "created",
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        index_status_output_path=index_status_output_path,
        expected_payload=expected_payload,
        card_json_path=run_dir / CARD_JSON_REF,
        original_card_payload=card_artifact.payload,
        original_card_bytes=card_artifact.raw_bytes,
        refreshed_card_payload=refreshed_card_payload,
    )


def _apply_planned_index_write(plan: _PlannedIndexWrite) -> IndexFixtureWriteResult:
    with _exclusive_card_index_lock(plan.run_dir):
        _require_unchanged_card(plan)
        status = _ensure_expected_index(plan)
        if plan.refreshed_card_payload is not None:
            _commit_card_refresh(plan)
        _load_and_validate_final_join(plan)
    return IndexFixtureWriteResult(
        paper_id=plan.paper_id,
        run_id=plan.run_id,
        status=status,
        run_dir=plan.run_dir,
        index_status_path=plan.index_status_output_path,
    )


def _require_unchanged_card(plan: _PlannedIndexWrite) -> None:
    current = load_paper_card_artifact(plan.card_json_path)
    if (
        current.payload != plan.original_card_payload
        or current.raw_bytes != plan.original_card_bytes
    ):
        raise MillefeuilleContractError(
            f"paper card changed before index commit: {plan.card_json_path}"
        )


def _card_index_transaction_dir(plan: _PlannedIndexWrite) -> Path:
    digest = hashlib.sha256()
    for label, payload in (
        (b"paper-id", plan.paper_id.encode("utf-8")),
        (b"run-id", plan.run_id.encode("utf-8")),
        (b"planned-card", plan.original_card_bytes),
        (b"index-status", canonical_json_bytes(plan.expected_payload)),
        (
            b"observed-card",
            (
                b""
                if plan.refreshed_card_payload is None
                else canonical_json_bytes(plan.refreshed_card_payload)
            ),
        ),
    ):
        digest.update(len(label).to_bytes(2, "big"))
        digest.update(label)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return plan.run_dir / CARD_INDEX_TRANSACTION_ROOT_REF / digest.hexdigest()


def _ensure_expected_index(plan: _PlannedIndexWrite) -> str:
    expected_bytes = canonical_json_bytes(plan.expected_payload)
    if plan.index_status_output_path.exists():
        _capture_expected_index(
            plan.index_status_output_path,
            expected_bytes,
            "existing retrieval index status",
        )
        return "existing"

    transaction_dir = _card_index_transaction_dir(plan)
    _ensure_real_directory(transaction_dir, "card/index transaction directory")
    _ensure_real_directory(
        plan.index_status_output_path.parent,
        "retrieval index status parent",
    )
    staged_path = transaction_dir / "index-status.json"
    staged_path = _stage_exact_bytes(
        staged_path,
        expected_bytes,
        label="retrieval index status",
    )
    status = "created"
    try:
        _move_file_no_replace(
            staged_path,
            plan.index_status_output_path,
        )
    except FileExistsError:
        status = "existing"

    current = _capture_expected_index(
        plan.index_status_output_path,
        expected_bytes,
        "retrieval index status after publication",
    )
    _ensure_independent_index_evidence(
        staged_path=staged_path,
        expected_bytes=expected_bytes,
        canonical=current,
    )
    return status


def _capture_expected_index(
    path: Path,
    expected_bytes: bytes,
    label: str,
) -> _CapturedFileEntry:
    current = _capture_file_entry(path, label)
    if current.raw_bytes != expected_bytes:
        raise MillefeuilleContractError(f"{label} bytes drifted: {path}")
    return current


def _ensure_independent_index_evidence(
    *,
    staged_path: Path,
    expected_bytes: bytes,
    canonical: _CapturedFileEntry,
) -> None:
    evidence_path = _stage_exact_bytes(
        staged_path,
        expected_bytes,
        label="retrieval index status",
    )
    evidence = _capture_expected_index(
        evidence_path,
        expected_bytes,
        "retrieval index transaction evidence",
    )
    if evidence.file_key == canonical.file_key:
        raise MillefeuilleContractError(
            "retrieval index transaction evidence must not alias the canonical "
            f"index: {evidence_path}"
        )


def _commit_card_refresh(plan: _PlannedIndexWrite) -> None:
    refreshed_payload = plan.refreshed_card_payload
    if refreshed_payload is None:
        return
    refreshed_bytes = canonical_json_bytes(refreshed_payload)
    transaction_dir = _card_index_transaction_dir(plan)
    _ensure_real_directory(transaction_dir, "card/index transaction directory")
    staged_path = _stage_exact_bytes(
        transaction_dir / "card-exchange.json",
        refreshed_bytes,
        label="observed paper card",
    )
    # These are deliberately the final pre-commit reads. The atomic capture
    # below validates the entry actually displaced at the linearization point.
    _require_unchanged_card(plan)
    _require_expected_index(plan)
    _before_card_exchange(plan)
    replacement_snapshot = _capture_file_entry(
        staged_path,
        "card replacement",
    )
    if replacement_snapshot.raw_bytes != refreshed_bytes:
        raise MillefeuilleContractError(
            "card replacement changed immediately before the atomic "
            "index-state commit boundary"
        )
    displaced = _atomic_capture_replace(
        target=plan.card_json_path,
        replacement=staged_path,
        displaced_path=transaction_dir / "displaced-card.json",
    )
    displaced_snapshot: _CapturedFileEntry | None = None
    try:
        displaced_snapshot = _capture_file_entry(
            displaced.path,
            "displaced paper card",
        )
        if displaced_snapshot.raw_bytes != plan.original_card_bytes:
            raise MillefeuilleContractError(
                "paper card changed at the atomic index-state commit boundary"
            )
        current = _capture_file_entry(
            plan.card_json_path,
            "installed paper card",
        )
        if (
            current.raw_bytes != refreshed_bytes
            or current.file_key != replacement_snapshot.file_key
        ):
            raise MillefeuilleContractError(
                "paper card replacement identity or bytes changed at the "
                "atomic commit boundary"
            )
        _require_expected_index(plan)
    except Exception as exc:
        if displaced_snapshot is None:
            raise MillefeuilleContractError(
                "could not securely capture the paper card displaced at the "
                "atomic index-state commit boundary; transaction evidence was "
                f"retained at {transaction_dir}"
            ) from exc
        _restore_displaced_card(
            target=plan.card_json_path,
            displaced=displaced,
            displaced_snapshot=displaced_snapshot,
            expected_replacement_bytes=refreshed_bytes,
        )
        raise


def _require_expected_index(plan: _PlannedIndexWrite) -> None:
    expected_bytes = canonical_json_bytes(plan.expected_payload)
    _capture_expected_index(
        plan.index_status_output_path,
        expected_bytes,
        "retrieval index status before card refresh",
    )


def _load_and_validate_final_join(plan: _PlannedIndexWrite) -> None:
    load_and_validate_canonical_card_index(
        card_path=plan.card_json_path,
        index_path=plan.index_status_output_path,
        paper_id=plan.paper_id,
        run_id=plan.run_id,
        source_hash=plan.expected_payload["source_hash"],
        selected_fulltext_path=plan.source_pack_dir / ROUTE_MARKDOWN_REF,
        summary_path=plan.run_dir / SUMMARY_ARTIFACT_REF,
    )


def _before_card_exchange(_plan: _PlannedIndexWrite) -> None:
    """Test seam immediately before the atomic capture-and-replace operation."""


@contextmanager
def _exclusive_card_index_lock(run_dir: Path):
    _require_real_directory(run_dir, "card/index run directory")
    lock_path = run_dir / ".card-index.lock"
    created = False
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(lock_path, flags | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(lock_path, flags, 0o600)
    try:
        opened_stat = _require_same_regular_entry(lock_path, fd, "card/index lock")
        if created:
            os.write(fd, b"\0")
            os.fsync(fd)
        elif opened_stat.st_size < 1:
            raise MillefeuilleContractError(
                f"card/index lock is incomplete: {lock_path}"
            )
        _lock_fd_nonblocking(fd, lock_path)
        try:
            _require_same_regular_entry(lock_path, fd, "card/index lock")
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


def _lock_fd_nonblocking(fd: int, lock_path: Path) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise MillefeuilleContractError(
            f"card/index commit is already locked: {lock_path}"
        ) from exc


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _require_same_regular_entry(
    path: Path,
    fd: int,
    label: str,
) -> os.stat_result:
    opened = os.fstat(fd)
    try:
        named = os.lstat(path)
    except OSError as exc:
        raise MillefeuilleContractError(f"{label} changed: {path}") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise MillefeuilleContractError(
            f"{label} must be one singly linked regular file: {path}"
        )
    return opened


def _stage_exact_bytes(staged_path: Path, payload: bytes, *, label: str) -> Path:
    parent_identity = _require_real_directory(
        staged_path.parent,
        f"{label} transaction parent",
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(staged_path, flags, 0o600)
    except FileExistsError:
        staged = (
            load_paper_card_artifact(staged_path)
            if label == "observed paper card"
            else load_retrieval_index_artifact(staged_path)
        )
        if staged.raw_bytes != payload:
            raise MillefeuilleContractError(
                f"existing staged {label} bytes drifted: {staged_path}"
            ) from None
        return staged_path
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise MillefeuilleContractError(
                    f"could not stage complete {label}: {staged_path}"
                )
            offset += written
        os.fsync(fd)
    except Exception:
        os.close(fd)
        raise
    else:
        os.close(fd)
    _require_unchanged_directory(
        staged_path.parent,
        parent_identity,
        f"{label} transaction parent",
    )
    staged = (
        load_paper_card_artifact(staged_path)
        if label == "observed paper card"
        else load_retrieval_index_artifact(staged_path)
    )
    if staged.raw_bytes != payload:
        raise MillefeuilleContractError(f"staged {label} bytes changed: {staged_path}")
    return staged_path


def _ensure_real_directory(path: Path, label: str) -> os.stat_result:
    target = path.absolute()
    anchor = Path(target.anchor)
    current = anchor
    for part in target.parts[1:]:
        current /= part
        try:
            identity = os.lstat(current)
        except FileNotFoundError:
            with suppress(FileExistsError):
                os.mkdir(current, mode=0o700)
            try:
                identity = os.lstat(current)
            except OSError as exc:
                raise MillefeuilleContractError(
                    f"could not create or inspect {label}: {path}"
                ) from exc
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not inspect {label}: {path}"
            ) from exc
        if (
            stat.S_ISLNK(identity.st_mode)
            or _is_windows_reparse_point(identity)
            or not stat.S_ISDIR(identity.st_mode)
        ):
            raise MillefeuilleContractError(
                f"{label} must not contain symbolic links, reparse points, "
                f"or non-directories: {path}"
            )
    return _require_real_directory(target, label)


def _require_real_directory(path: Path, label: str) -> os.stat_result:
    target = path.absolute()
    anchor = Path(target.anchor)
    current = anchor
    identity: os.stat_result | None = None
    for part in target.parts[1:]:
        current /= part
        try:
            identity = os.lstat(current)
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not inspect {label}: {path}"
            ) from exc
        if (
            stat.S_ISLNK(identity.st_mode)
            or _is_windows_reparse_point(identity)
            or not stat.S_ISDIR(identity.st_mode)
        ):
            raise MillefeuilleContractError(
                f"{label} must not contain symbolic links, reparse points, "
                f"or non-directories: {path}"
            )
    if identity is None:
        identity = os.lstat(anchor)
    return identity


def _is_windows_reparse_point(value: os.stat_result) -> bool:
    if os.name != "nt":
        return False
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_tag = getattr(value, "st_reparse_tag", 0)
    return bool(attributes & reparse_attribute) or bool(reparse_tag)


def _require_unchanged_directory(
    path: Path,
    expected: os.stat_result,
    label: str,
) -> None:
    current = _require_real_directory(path, label)
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise MillefeuilleContractError(f"{label} changed: {path}")


def _atomic_capture_replace(
    *,
    target: Path,
    replacement: Path,
    displaced_path: Path,
) -> _DisplacedCardEntry:
    _require_real_directory(target.parent, "paper card parent")
    _require_real_directory(replacement.parent, "card/index transaction directory")
    if os.name == "nt":
        _require_non_reparse_regular_entry(target, "paper card")
        _require_non_reparse_regular_entry(replacement, "card replacement")
        _require_missing_entry(displaced_path, "displaced paper card")
        _windows_replace_file(target, replacement, displaced_path)
        return _DisplacedCardEntry(
            path=displaced_path,
            mechanism="windows-replace",
            rejected_path=replacement,
        )
    if sys.platform.startswith("linux"):
        _linux_exchange(target, replacement)
        return _DisplacedCardEntry(
            path=replacement,
            mechanism="exchange",
            rejected_path=replacement,
        )
    if sys.platform == "darwin":
        _darwin_exchange(target, replacement)
        return _DisplacedCardEntry(
            path=replacement,
            mechanism="exchange",
            rejected_path=replacement,
        )
    raise MillefeuilleContractError(
        "atomic card/index state exchange is unavailable on this platform"
    )


def _move_file_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        try:
            os.rename(source, destination)
        except FileExistsError:
            raise
        except OSError as exc:
            raise MillefeuilleContractError(
                "could not atomically publish retrieval index status "
                f"{destination}: {exc}"
            ) from exc
        return
    if sys.platform.startswith("linux"):
        _linux_move_no_replace(source, destination)
        return
    if sys.platform == "darwin":
        _darwin_move_no_replace(source, destination)
        return
    raise MillefeuilleContractError(
        "atomic no-replace index publication is unavailable on this platform"
    )


def _linux_move_no_replace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise MillefeuilleContractError(
            "atomic no-replace index publication requires renameat2 on Linux"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        raise MillefeuilleContractError(
            "could not atomically publish retrieval index status "
            f"{destination}: errno {error}"
        )


def _darwin_move_no_replace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        raise MillefeuilleContractError(
            "atomic no-replace index publication requires renamex_np on macOS"
        )
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    if renamex_np(os.fsencode(source), os.fsencode(destination), 4) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        raise MillefeuilleContractError(
            "could not atomically publish retrieval index status "
            f"{destination}: errno {error}"
        )


def _restore_displaced_card(
    *,
    target: Path,
    displaced: _DisplacedCardEntry,
    displaced_snapshot: _CapturedFileEntry,
    expected_replacement_bytes: bytes,
) -> None:
    _before_card_rollback(displaced)
    _require_captured_file_entry(
        displaced_snapshot,
        "displaced paper card",
    )
    if displaced.mechanism == "windows-replace":
        _require_missing_entry(displaced.rejected_path, "rejected paper card")
        _windows_replace_file(target, displaced.path, displaced.rejected_path)
    else:
        _exchange_paths(target, displaced.path)
    _after_card_rollback(displaced)
    restored = _capture_file_entry(target, "restored paper card")
    rejected = _capture_file_entry(
        displaced.rejected_path,
        "rejected paper card",
    )
    if (
        restored.raw_bytes != displaced_snapshot.raw_bytes
        or restored.file_key != displaced_snapshot.file_key
    ):
        raise MillefeuilleContractError(
            "paper card rollback did not restore the exact displaced entry; "
            "transaction evidence was preserved at "
            f"{displaced.rejected_path.parent}"
        )
    if rejected.raw_bytes != expected_replacement_bytes:
        raise MillefeuilleContractError(
            "paper card changed again during rollback; unexpected bytes were "
            f"preserved at {displaced.rejected_path}"
        )


def _before_card_rollback(_displaced: _DisplacedCardEntry) -> None:
    """Test seam after displaced capture and immediately before rollback."""


def _after_card_rollback(_displaced: _DisplacedCardEntry) -> None:
    """Test seam after rollback exchange and before restored-entry validation."""


def _require_missing_entry(path: Path, label: str) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise MillefeuilleContractError(f"could not inspect {label}: {path}") from exc
    raise MillefeuilleContractError(f"{label} already exists: {path}")


def _require_non_reparse_regular_entry(path: Path, label: str) -> os.stat_result:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise MillefeuilleContractError(f"could not inspect {label}: {path}") from exc
    if (
        stat.S_ISLNK(value.st_mode)
        or _is_windows_reparse_point(value)
        or not stat.S_ISREG(value.st_mode)
    ):
        raise MillefeuilleContractError(
            f"{label} must be a non-reparse regular file: {path}"
        )
    return value


def _capture_file_entry(path: Path, label: str) -> _CapturedFileEntry:
    _require_real_directory(path.parent, f"{label} parent")
    flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag_name, 0)
    try:
        named_before = os.lstat(path)
        if (
            stat.S_ISLNK(named_before.st_mode)
            or _is_windows_reparse_point(named_before)
            or not stat.S_ISREG(named_before.st_mode)
        ):
            raise MillefeuilleContractError(
                f"{label} must be a non-reparse regular file: {path}"
            )
        if named_before.st_nlink != 1:
            raise MillefeuilleContractError(f"{label} must be singly linked: {path}")
        if named_before.st_size > CARD_INDEX_TRANSACTION_MAX_CARD_BYTES:
            raise MillefeuilleContractError(
                f"{label} exceeds the "
                f"{CARD_INDEX_TRANSACTION_MAX_CARD_BYTES}-byte card transaction "
                f"limit: {path}"
            )
        fd = os.open(path, flags)
    except MillefeuilleContractError:
        raise
    except OSError as exc:
        raise MillefeuilleContractError(f"could not open {label}: {path}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise MillefeuilleContractError(f"{label} is not a regular file: {path}")
        if opened.st_nlink != 1:
            raise MillefeuilleContractError(f"{label} must be singly linked: {path}")
        if opened.st_size > CARD_INDEX_TRANSACTION_MAX_CARD_BYTES:
            raise MillefeuilleContractError(
                f"{label} exceeds the "
                f"{CARD_INDEX_TRANSACTION_MAX_CARD_BYTES}-byte card transaction "
                f"limit: {path}"
            )
        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > CARD_INDEX_TRANSACTION_MAX_CARD_BYTES:
                raise MillefeuilleContractError(
                    f"{label} exceeded the "
                    f"{CARD_INDEX_TRANSACTION_MAX_CARD_BYTES}-byte card "
                    f"transaction limit while read: {path}"
                )
            chunks.append(chunk)
        raw_bytes = b"".join(chunks)
        final_opened = os.fstat(fd)
        named_after = os.lstat(path)
        expected_identity = _file_entry_identity(named_before)
        if (
            _file_entry_identity(opened) != expected_identity
            or _file_entry_identity(final_opened) != expected_identity
            or _file_entry_identity(named_after) != expected_identity
            or total_bytes != opened.st_size
            or len(raw_bytes) != total_bytes
        ):
            raise MillefeuilleContractError(
                f"{label} changed while read: {path}; "
                f"named-before={expected_identity!r}, "
                f"opened={_file_entry_identity(opened)!r}, "
                f"final-opened={_file_entry_identity(final_opened)!r}, "
                f"named-after={_file_entry_identity(named_after)!r}"
            )
        return _CapturedFileEntry(
            path=path,
            raw_bytes=raw_bytes,
            identity=expected_identity,
            file_key=(opened.st_dev, opened.st_ino),
        )
    except OSError as exc:
        raise MillefeuilleContractError(f"could not read {label}: {path}") from exc
    finally:
        os.close(fd)


def _require_captured_file_entry(
    expected: _CapturedFileEntry,
    label: str,
) -> None:
    current = _capture_file_entry(expected.path, label)
    if current.identity != expected.identity or current.raw_bytes != expected.raw_bytes:
        raise MillefeuilleContractError(
            f"captured {label} changed before rollback: {expected.path}"
        )


def _file_entry_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
    )


def _exchange_paths(first: Path, second: Path) -> None:
    if sys.platform.startswith("linux"):
        _linux_exchange(first, second)
    elif sys.platform == "darwin":
        _darwin_exchange(first, second)
    else:
        raise MillefeuilleContractError(
            "atomic card/index state exchange is unavailable on this platform"
        )


def _windows_replace_file(target: Path, replacement: Path, backup: Path) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace_file.restype = ctypes.c_int
    ctypes.set_last_error(0)
    if not replace_file(str(target), str(replacement), str(backup), 1, None, None):
        error = ctypes.get_last_error()
        raise MillefeuilleContractError(
            f"atomic paper card replacement failed with Windows error {error}: {target}"
        )


def _linux_exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise MillefeuilleContractError(
            "atomic paper card exchange requires renameat2 on Linux"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(first),
        -100,
        os.fsencode(second),
        2,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise MillefeuilleContractError(
            f"atomic paper card exchange failed with errno {error}: {first}"
        )


def _darwin_exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is None:
        raise MillefeuilleContractError(
            "atomic paper card exchange requires renamex_np on macOS"
        )
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    if renamex_np(os.fsencode(first), os.fsencode(second), 2) != 0:
        error = ctypes.get_errno()
        raise MillefeuilleContractError(
            f"atomic paper card exchange failed with errno {error}: {first}"
        )


def _materialize_index_payload(
    *,
    fixture_payload: dict[str, Any],
    source_pack_dir: Path,
    run_dir: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
    index_dir: Path,
) -> dict[str, Any]:
    record = RetrievalIndexRecord.from_dict(fixture_payload)
    payload = record.to_dict()
    payload["paper_id"] = paper_id
    payload["run_id"] = run_id
    payload["source_hash"] = source_hash
    payload["selected_fulltext_ref"] = _relative_ref(
        source_pack_dir / ROUTE_MARKDOWN_REF,
        index_dir,
    )
    payload["summary_ref"] = _relative_ref(run_dir / SUMMARY_ARTIFACT_REF, index_dir)
    payload["paper_card_ref"] = _relative_ref(run_dir / CARD_JSON_REF, index_dir)
    return payload


def _plan_card_index_refresh(
    *,
    card_payload: dict[str, Any],
    index_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if card_payload["schema_version"] == PAPER_CARD_SCHEMA_V1:
        return None
    if card_payload["schema_version"] != PAPER_CARD_SCHEMA_V2:
        raise MillefeuilleContractError(
            f"unsupported paper card schema_version {card_payload['schema_version']!r}"
        )
    desired_state = observed_index_state(index_payload)
    current_state = card_payload["index_state"]
    if current_state["phase"] == "observed":
        if current_state != desired_state:
            raise MillefeuilleContractError(
                "paper card observed index_state conflicts with retrieval index status"
            )
        return None
    planned_lanes = [entry["lane"] for entry in current_state["lanes"]]
    observed_lanes = [entry["lane"] for entry in desired_state["lanes"]]
    if planned_lanes != observed_lanes:
        raise MillefeuilleContractError(
            "paper card planned index lanes conflict with retrieval index status"
        )
    refreshed_payload = copy.deepcopy(card_payload)
    refreshed_payload["index_state"] = desired_state
    PaperCardRecord.from_dict(refreshed_payload)
    return refreshed_payload


def validate_observed_card_index_state(
    card_payload: dict[str, Any],
    index_payload: dict[str, Any],
) -> None:
    """Validate the v0.2 card's observed state against its canonical index result."""

    _validate_observed_card_index_state(card_payload, index_payload)


def _existing_index_status(
    *,
    index_status_output_path: Path,
    expected_payload: dict[str, Any],
) -> str | None:
    if not index_status_output_path.exists():
        return None
    existing = load_retrieval_index_artifact(index_status_output_path)
    if (
        existing.payload != expected_payload
        or existing.raw_bytes != canonical_json_bytes(expected_payload)
    ):
        raise MillefeuilleContractError(
            f"existing retrieval index status drift for {index_status_output_path}"
        )
    return "existing"


def _resolve_source_pack_dir(
    *,
    source_pack_root: Path,
    item_key: str,
    attachment_key: str,
    canonical_filename: str,
    expected_sha256: str,
    zotero_version: int | None,
    paper_id: str | None,
) -> tuple[Path, str]:
    resolved_paper_id = paper_id or paper_id_for_zotero_item_key(item_key)
    source_pack_dir = source_pack_root / "zotero" / resolved_paper_id
    manifest_path = source_pack_dir / "manifest.json"
    manifest = load_source_pack_manifest(manifest_path)
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise MillefeuilleContractError(
            f"source-pack manifest identity must be an object: {manifest_path}"
        )
    if identity.get("zotero_item_key") != item_key:
        raise MillefeuilleContractError(
            f"source-pack item_key drift for {resolved_paper_id}"
        )
    if identity.get("zotero_attachment_key") != attachment_key:
        raise MillefeuilleContractError(
            f"source-pack attachment_key drift for {resolved_paper_id}"
        )
    if identity.get("canonical_filename") != canonical_filename:
        raise MillefeuilleContractError(
            f"source-pack canonical_filename drift for {resolved_paper_id}"
        )
    if zotero_version is not None and identity.get("zotero_version") not in (
        None,
        zotero_version,
    ):
        raise MillefeuilleContractError(
            f"source-pack zotero_version drift for {resolved_paper_id}"
        )
    source_hash = _normalize_source_hash(
        _required_string(manifest.get("source_hash"), "source_hash")
    )
    if source_hash != f"sha256:{expected_sha256}":
        raise MillefeuilleContractError(
            f"source-pack source hash drift for {resolved_paper_id}"
        )
    return source_pack_dir, source_hash


def _validate_route_dependency(*, source_pack_dir: Path, source_hash: str) -> None:
    route_payload = load_route_selection_sidecar(source_pack_dir / ROUTE_EVIDENCE_REF)
    if route_payload["source_hash"] != source_hash:
        raise MillefeuilleContractError(
            f"route selection source_hash drift for {source_pack_dir}"
        )
    selected_fulltext_path = source_pack_dir / ROUTE_MARKDOWN_REF
    if not selected_fulltext_path.is_file():
        raise MillefeuilleContractError(
            "retrieval index fixture requires selected fulltext: "
            f"{selected_fulltext_path}"
        )


def _validate_summary_dependency(
    *,
    run_dir: Path,
    paper_id: str,
    run_id: str,
) -> None:
    summary_path = run_dir / SUMMARY_ARTIFACT_REF
    payload = load_hierarchical_summary(summary_path)
    if payload["paper_id"] != paper_id:
        raise MillefeuilleContractError(
            f"hierarchical summary paper_id drift at {summary_path}"
        )
    if payload["run_id"] != run_id:
        raise MillefeuilleContractError(
            f"hierarchical summary run_id drift at {summary_path}"
        )
    summary_dir = summary_path.parent
    for summary in payload["summaries"]:
        text_ref = _required_string(summary.get("text_ref"), "text_ref")
        text_path = summary_dir / text_ref
        if not text_path.is_file():
            raise MillefeuilleContractError(
                f"retrieval index fixture requires summary text: {text_path}"
            )


def _validate_card_dependency(
    *,
    run_dir: Path,
    paper_id: str,
    run_id: str,
    source_hash: str,
) -> LoadedJsonArtifact:
    card_json_path = run_dir / CARD_JSON_REF
    artifact = load_paper_card_artifact(card_json_path)
    validate_paper_card_identity(
        artifact.payload,
        paper_id=paper_id,
        run_id=run_id,
        source_hash=source_hash,
    )
    return artifact


def _reject_duplicate_records(records: list[IndexFixtureEvidence]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.item_key, record.attachment_key)
        if key in seen:
            raise MillefeuilleContractError(
                "duplicate index fixture evidence for "
                f"{record.item_key}/{record.attachment_key}"
            )
        seen.add(key)


def _relative_ref(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start=start)).as_posix()


def _load_json_object(path: str | Path, kind: str) -> dict[str, Any]:
    return load_json_object_no_follow(path, kind)


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MillefeuilleContractError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise MillefeuilleContractError(f"{field_name} must not be empty")
    return stripped


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MillefeuilleContractError(f"{field_name} must be an integer")
    return value


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise MillefeuilleContractError("expected_sha256 must be 64 lowercase hex")
    return normalized


def _normalize_source_hash(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
        raise MillefeuilleContractError(
            "source_hash must be in sha256:<64 lowercase hex> form"
        )
    return normalized
