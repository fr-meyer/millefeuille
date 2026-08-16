"""Evidence-safe quarantine for abandoned unpublished local staging assets.

Inspection and planning are read-only.  Applying a plan requires an exact
MF-100 approved-live receipt and moves verified generations to a same-filesystem
quarantine with an atomic no-replace rename.  This module never deletes a
generation or interprets an unknown namespace entry as owned.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

from millefeuille.domain.live_receipts import (
    STAGING_CLEANUP_MAINTENANCE_DISPOSAL,
    STAGING_CLEANUP_MAINTENANCE_OPERATION,
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    LiveDisposalPolicy,
    LiveSelector,
    LiveTarget,
    ReceiptReplayState,
    build_approved_live_audit_record,
    load_approved_live_receipt,
    validate_approved_live_receipt,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.retrieve import (
    _RETRIEVAL_BATCH_TEMP_PREFIX,
    RETRIEVAL_BATCH_REPORT_REF,
    RETRIEVAL_BATCH_RESULT_REF,
    RETRIEVAL_BATCH_ROOT_REF,
)
from millefeuille.domain.secure_io import read_bytes_no_follow

BRIDGE_OWNERSHIP_SCHEMA_VERSION = "millefeuille-temporary-bridge-ownership/v0.1"
STAGING_CLEANUP_INSPECTION_SCHEMA_VERSION = (
    "millefeuille-staging-cleanup-inspection/v0.1"
)
STAGING_CLEANUP_PLAN_SCHEMA_VERSION = "millefeuille-staging-cleanup-plan/v0.1"
STAGING_CLEANUP_AUDIT_SCHEMA_VERSION = "millefeuille-staging-cleanup-audit/v0.1"
STAGING_CLEANUP_DISPOSITION_SCHEMA_VERSION = (
    "millefeuille-staging-cleanup-disposition/v0.1"
)

STAGING_CLEANUP_OPERATION = STAGING_CLEANUP_MAINTENANCE_OPERATION
STAGING_CLEANUP_DISPOSAL = STAGING_CLEANUP_MAINTENANCE_DISPOSAL
STAGING_CLEANUP_STOP_CONDITIONS = (
    "candidate-drift",
    "concurrent-namespace-change",
    "first-error",
    "root-identity-drift",
    "stale-or-active-lock",
    "unsupported-atomic-primitive",
)

RETRIEVAL_STAGING_KIND = "retrieval-staging-generation"
TEMPORARY_BRIDGE_KIND = "temporary-bridge-generation"
BRIDGE_OWNERSHIP_MANIFEST = "ownership.json"
BRIDGE_TEMP_PREFIX = ".millefeuille-bridge.tmp-"

_BRIDGE_MANIFEST_MAX_BYTES = 65_536
_CLEANUP_PLAN_MAX_BYTES = 1_048_576
_CLEANUP_AUDIT_MAX_BYTES = 65_536
_AUDIT_PREFIX = ".mf106-audit-"
_AUDIT_SUFFIX = ".json"
_LOCK_NAME = ".mf106-quarantine.lock"
_HEX_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TEMP_SUFFIX_RE = re.compile(r"[0-9a-f]{16}\Z")
_SAFE_BATCH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_SAFE_PRODUCER_RE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9][a-z0-9-]*)*\Z")
_AUDIT_NAME_RE = re.compile(r"\.mf106-audit-([0-9a-f]{64})\.json\Z")
_JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)

_BRIDGE_FIELDS = frozenset(
    {
        "schema_version",
        "producer_id",
        "run_id",
        "bridge_id",
        "publication_state",
        "cleanup_state",
        "inventory",
        "integrity",
    }
)
_BRIDGE_INVENTORY_FIELDS = frozenset({"relative_path", "type", "size_bytes", "sha256"})
_INTEGRITY_FIELDS = frozenset({"algorithm", "content_digest"})
_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "operation",
        "roots",
        "source_namespace_identity",
        "quarantine_namespace_identity",
        "candidate_count",
        "candidates",
        "disposal",
        "stop_conditions",
        "integrity",
    }
)
_PLAN_ROOT_FIELDS = frozenset({"source", "quarantine"})
_PLAN_CANDIDATE_FIELDS = frozenset(
    {
        "kind",
        "relative_path",
        "generation_identity",
        "source_snapshot_identity",
        "quarantine_name",
    }
)
_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "receipt_identity",
        "receipt_digest",
        "request_digest",
        "plan_digest",
        "operation",
        "candidate_count",
        "evaluated_at",
        "secret_material_persisted",
        "integrity",
    }
)
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "headers",
        "oauth_token",
        "password",
        "payload",
        "pdf_bytes",
        "private_key",
        "provider_payload",
        "raw_response",
        "refresh_token",
        "secret",
    }
)
_FORBIDDEN_VALUE_MARKERS = (
    re.compile(r"Authorization:\s*(?:Bearer|Basic)", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"data:application/pdf", re.IGNORECASE),
    re.compile(r"%PDF-"),
    re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    re.compile(
        r"(?:[?&]|\b)(?:access_token|api_key|password|refresh_token|secret|token)="
        r"[^\s&]+",
        re.IGNORECASE,
    ),
)


class _CandidateUnverifiedError(Exception):
    def __init__(self, *reason_codes: str):
        super().__init__(reason_codes[0] if reason_codes else "unverified")
        self.reason_codes = tuple(sorted(set(reason_codes or ("unverified",))))


class CleanupApplyError(MillefeuilleContractError):
    """An apply failure that carries safe disposition evidence."""

    def __init__(self, disposition: dict[str, Any], reason: str):
        super().__init__(reason)
        self.disposition = disposition


@dataclass(frozen=True)
class _RootContext:
    source: Path
    quarantine: Path
    source_stat: os.stat_result
    quarantine_stat: os.stat_result
    source_identity: str
    quarantine_identity: str


@dataclass(frozen=True)
class _CandidateSnapshot:
    kind: str
    relative_path: str
    generation_identity: str
    source_snapshot_identity: str
    quarantine_name: str

    def to_plan_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "generation_identity": self.generation_identity,
            "source_snapshot_identity": self.source_snapshot_identity,
            "quarantine_name": self.quarantine_name,
        }

    def to_inspection_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "generation_identity": self.generation_identity,
        }


@dataclass(frozen=True)
class _UnverifiedCandidate:
    namespace: str
    opaque_identity: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "opaque_identity": self.opaque_identity,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class _ScanState:
    roots: _RootContext
    candidates: tuple[_CandidateSnapshot, ...]
    unverified: tuple[_UnverifiedCandidate, ...]
    source_namespace: dict[str, tuple[str, ...]]
    quarantine_namespace: dict[str, tuple[str, ...]]

    @property
    def source_namespace_identity(self) -> str:
        return _namespace_identity(self.source_namespace)

    @property
    def quarantine_namespace_identity(self) -> str:
        return _namespace_identity(self.quarantine_namespace)


@dataclass(frozen=True)
class _PinnedRoots:
    source_fd: int
    quarantine_fd: int


@dataclass(frozen=True)
class _PinnedCandidate:
    candidate: dict[str, Any]
    parent_fd: int
    generation_fd: int
    source_name: str


def compute_bridge_ownership_digest(payload: dict[str, Any]) -> str:
    """Return the content identity for a strict bridge ownership manifest."""

    body = dict(payload)
    body.pop("integrity", None)
    return _digest_object(body)


def compute_staging_cleanup_plan_digest(payload: dict[str, Any]) -> str:
    """Return the content identity for a strict cleanup plan."""

    body = dict(payload)
    body.pop("integrity", None)
    return _digest_object(body)


def inspect_staging_cleanup(
    *,
    source_root: str | Path,
    quarantine_root: str | Path,
) -> dict[str, Any]:
    """Inspect known temporary namespaces without modifying local state."""

    state = _scan(source_root=source_root, quarantine_root=quarantine_root)
    capabilities = staging_cleanup_capabilities()
    capabilities["same_device_stable_roots"] = (
        state.roots.source_stat.st_dev == state.roots.quarantine_stat.st_dev
        and state.roots.source_stat.st_ino != 0
        and state.roots.quarantine_stat.st_ino != 0
    )
    capabilities["apply_supported"] = _roots_support_atomic_quarantine(state.roots)
    return {
        "schema_version": STAGING_CLEANUP_INSPECTION_SCHEMA_VERSION,
        "operation": STAGING_CLEANUP_OPERATION,
        "roots": {
            "source": state.roots.source_identity,
            "quarantine": state.roots.quarantine_identity,
        },
        "source_namespace_identity": state.source_namespace_identity,
        "quarantine_namespace_identity": state.quarantine_namespace_identity,
        "capabilities": capabilities,
        "eligible_candidates": [
            candidate.to_inspection_dict() for candidate in state.candidates
        ],
        "unverified_candidates": [
            candidate.to_dict() for candidate in state.unverified
        ],
        "counts": {
            "eligible": len(state.candidates),
            "unverified": len(state.unverified),
        },
        "default_action": "inspect-only",
        "private_bytes_serialized": False,
        "secret_material_persisted": False,
    }


def build_staging_cleanup_plan(
    *,
    source_root: str | Path,
    quarantine_root: str | Path,
    run_id: str,
    candidate_paths: Sequence[str],
    expected_candidate_count: int,
    disposal: str,
    stop_conditions: Sequence[str],
) -> dict[str, Any]:
    """Build a deterministic, read-only, content-addressed cleanup plan."""

    _safe_identity(run_id, "run_id")
    if disposal != STAGING_CLEANUP_DISPOSAL:
        raise MillefeuilleContractError("cleanup disposal policy is unsupported")
    normalized_stops = tuple(stop_conditions)
    if normalized_stops != tuple(sorted(set(normalized_stops))):
        raise MillefeuilleContractError(
            "cleanup stop_conditions must be sorted and unique"
        )
    if normalized_stops != STAGING_CLEANUP_STOP_CONDITIONS:
        raise MillefeuilleContractError(
            "cleanup stop_conditions must match the supported fail-closed set"
        )
    if (
        isinstance(expected_candidate_count, bool)
        or not isinstance(expected_candidate_count, int)
        or expected_candidate_count < 1
        or expected_candidate_count > 256
    ):
        raise MillefeuilleContractError(
            "expected_candidate_count must be an integer from 1 through 256"
        )
    normalized_paths = tuple(
        sorted(_safe_relative_path(value) for value in candidate_paths)
    )
    if len(normalized_paths) != len(set(normalized_paths)):
        raise MillefeuilleContractError("cleanup candidates must be unique")
    if len(normalized_paths) != expected_candidate_count:
        raise MillefeuilleContractError(
            "cleanup candidate count does not match expected_candidate_count"
        )

    state = _scan(source_root=source_root, quarantine_root=quarantine_root)
    if state.roots.source_stat.st_dev != state.roots.quarantine_stat.st_dev:
        raise MillefeuilleContractError(
            "cleanup source and quarantine roots must be on the same filesystem"
        )
    available = {candidate.relative_path: candidate for candidate in state.candidates}
    try:
        selected = tuple(
            sorted(
                (available[path] for path in normalized_paths),
                key=lambda candidate: (candidate.kind, candidate.relative_path),
            )
        )
    except KeyError as exc:
        raise MillefeuilleContractError(
            "cleanup plan contains an unverified or unavailable candidate"
        ) from exc
    colliding_names = {
        candidate.quarantine_name
        for candidate in selected
        if candidate.quarantine_name in state.quarantine_namespace
    }
    if colliding_names:
        raise MillefeuilleContractError("cleanup quarantine destination already exists")

    payload: dict[str, Any] = {
        "schema_version": STAGING_CLEANUP_PLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "operation": STAGING_CLEANUP_OPERATION,
        "roots": {
            "source": state.roots.source_identity,
            "quarantine": state.roots.quarantine_identity,
        },
        "source_namespace_identity": state.source_namespace_identity,
        "quarantine_namespace_identity": state.quarantine_namespace_identity,
        "candidate_count": len(selected),
        "candidates": [candidate.to_plan_dict() for candidate in selected],
        "disposal": disposal,
        "stop_conditions": list(normalized_stops),
    }
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_staging_cleanup_plan_digest(payload),
    }
    _validate_plan_payload(payload)
    return payload


def load_staging_cleanup_plan(path: str | Path) -> dict[str, Any]:
    """Securely load one strict content-addressed cleanup plan."""

    payload_bytes = read_bytes_no_follow(
        path,
        "staging cleanup plan",
        max_bytes=_CLEANUP_PLAN_MAX_BYTES,
    )
    payload = _load_json_object(payload_bytes, "staging cleanup plan")
    _validate_plan_payload(payload)
    return payload


def staging_cleanup_capabilities() -> dict[str, Any]:
    """Return sanitized platform capability evidence for cleanup."""

    if os.name == "nt":
        platform = "windows"
    elif os.name == "posix":
        platform = "posix"
    else:
        platform = "unsupported"
    pinned_executor = _has_pinned_directory_primitives()
    return {
        "platform": platform,
        "atomic_no_replace_quarantine": pinned_executor,
        "pinned_directory_mutation": pinned_executor,
        "posix_batch_lock": _has_posix_batch_lock(),
        "reparse_point_detection": os.name == "nt",
    }


def _scan(
    *,
    source_root: str | Path,
    quarantine_root: str | Path,
    held_lock_fd: int | None = None,
    held_lock_parent_fd: int | None = None,
) -> _ScanState:
    roots = _root_context(source_root, quarantine_root)
    candidates: list[_CandidateSnapshot] = []
    unverified: list[_UnverifiedCandidate] = []
    source_namespace: dict[str, tuple[str, ...]] = {}

    root_entries = _stable_directory_entries(roots.source)
    for name, record in root_entries.items():
        if not name.startswith(BRIDGE_TEMP_PREFIX):
            continue
        source_namespace[f"bridge:{name}"] = record
        opaque = _opaque_namespace_identity("bridge", name, record)
        suffix = name.removeprefix(BRIDGE_TEMP_PREFIX)
        if _TEMP_SUFFIX_RE.fullmatch(suffix) is None:
            unverified.append(
                _UnverifiedCandidate(
                    namespace="bridge",
                    opaque_identity=opaque,
                    reason_codes=("unsupported-namespace",),
                )
            )
            continue
        relative = name
        try:
            candidates.append(
                _snapshot_bridge_candidate(
                    roots.source / name,
                    relative_path=relative,
                )
            )
        except _CandidateUnverifiedError as exc:
            unverified.append(
                _UnverifiedCandidate(
                    namespace="bridge",
                    opaque_identity=opaque,
                    reason_codes=exc.reason_codes,
                )
            )

    batch_root = roots.source / RETRIEVAL_BATCH_ROOT_REF
    if _entry_exists_no_follow(batch_root):
        try:
            batch_root_stat = _require_plain_directory(
                batch_root,
                label="retrieval batch namespace",
            )
        except MillefeuilleContractError:
            batch_root_record = _safe_stat_record(batch_root)
            unverified.append(
                _UnverifiedCandidate(
                    namespace="retrieval",
                    opaque_identity=_opaque_namespace_identity(
                        "retrieval-root",
                        RETRIEVAL_BATCH_ROOT_REF.as_posix(),
                        batch_root_record,
                    ),
                    reason_codes=("link-reparse-or-nondirectory",),
                )
            )
        else:
            source_namespace["retrieval-root"] = _stat_record(batch_root_stat)
            batch_entries = _stable_directory_entries(batch_root)
            for batch_name, batch_record in batch_entries.items():
                source_namespace[f"retrieval-batch:{batch_name}"] = batch_record
                batch_path = batch_root / batch_name
                if _SAFE_BATCH_ID_RE.fullmatch(batch_name) is None:
                    continue
                try:
                    _require_plain_directory(
                        batch_path,
                        label="retrieval batch directory",
                    )
                except MillefeuilleContractError:
                    unverified.append(
                        _UnverifiedCandidate(
                            namespace="retrieval",
                            opaque_identity=_opaque_namespace_identity(
                                "retrieval-batch",
                                batch_name,
                                batch_record,
                            ),
                            reason_codes=("link-reparse-or-nondirectory",),
                        )
                    )
                    continue
                try:
                    generation_entries = _stable_directory_entries(batch_path)
                except MillefeuilleContractError:
                    unverified.append(
                        _UnverifiedCandidate(
                            namespace="retrieval",
                            opaque_identity=_opaque_namespace_identity(
                                "retrieval-batch",
                                batch_name,
                                batch_record,
                            ),
                            reason_codes=("unstable-namespace",),
                        )
                    )
                    continue
                for name, record in generation_entries.items():
                    source_namespace[f"retrieval:{batch_name}/{name}"] = record
                    if not name.startswith(_RETRIEVAL_BATCH_TEMP_PREFIX):
                        continue
                    opaque = _opaque_namespace_identity(
                        "retrieval",
                        f"{batch_name}/{name}",
                        record,
                    )
                    suffix = name.removeprefix(_RETRIEVAL_BATCH_TEMP_PREFIX)
                    if _TEMP_SUFFIX_RE.fullmatch(suffix) is None:
                        unverified.append(
                            _UnverifiedCandidate(
                                namespace="retrieval",
                                opaque_identity=opaque,
                                reason_codes=("unsupported-namespace",),
                            )
                        )
                        continue
                    relative = (RETRIEVAL_BATCH_ROOT_REF / batch_name / name).as_posix()
                    try:
                        candidates.append(
                            _snapshot_retrieval_candidate(
                                batch_path / name,
                                relative_path=relative,
                            )
                        )
                    except _CandidateUnverifiedError as exc:
                        unverified.append(
                            _UnverifiedCandidate(
                                namespace="retrieval",
                                opaque_identity=opaque,
                                reason_codes=exc.reason_codes,
                            )
                        )

    quarantine_namespace = _cleanup_quarantine_namespace(
        roots.quarantine,
        held_lock_fd=held_lock_fd,
        held_lock_parent_fd=held_lock_parent_fd,
    )
    return _ScanState(
        roots=roots,
        candidates=tuple(sorted(candidates, key=lambda value: value.relative_path)),
        unverified=tuple(
            sorted(
                unverified,
                key=lambda value: (value.namespace, value.opaque_identity),
            )
        ),
        source_namespace=source_namespace,
        quarantine_namespace=quarantine_namespace,
    )


def _root_context(
    source_root: str | Path,
    quarantine_root: str | Path,
) -> _RootContext:
    source = _normalized_absolute_root(source_root, "source root")
    quarantine = _normalized_absolute_root(quarantine_root, "quarantine root")
    if _paths_overlap(source, quarantine):
        raise MillefeuilleContractError(
            "cleanup source and quarantine roots must be disjoint"
        )
    source_stat = _require_plain_directory(source, label="cleanup source root")
    quarantine_stat = _require_plain_directory(
        quarantine,
        label="cleanup quarantine root",
    )
    if _object_identity(source_stat) == _object_identity(quarantine_stat):
        raise MillefeuilleContractError(
            "cleanup source and quarantine roots must be distinct objects"
        )
    if source_stat.st_ino == 0 or quarantine_stat.st_ino == 0:
        raise MillefeuilleContractError(
            "cleanup roots require stable filesystem identities"
        )
    return _RootContext(
        source=source,
        quarantine=quarantine,
        source_stat=source_stat,
        quarantine_stat=quarantine_stat,
        source_identity=_root_identity(source, source_stat),
        quarantine_identity=_root_identity(quarantine, quarantine_stat),
    )


def _snapshot_retrieval_candidate(
    path: Path,
    *,
    relative_path: str,
) -> _CandidateSnapshot:
    try:
        before = _require_plain_directory(path, label="retrieval staging generation")
    except MillefeuilleContractError as exc:
        raise _CandidateUnverifiedError("link-reparse-or-nondirectory") from exc
    entries = _stable_directory_entries(path)
    expected_names = {
        RETRIEVAL_BATCH_RESULT_REF.name,
        RETRIEVAL_BATCH_REPORT_REF.name,
    }
    if set(entries) != expected_names:
        raise _CandidateUnverifiedError("unexpected-entries")
    if os.name == "posix" and stat.S_IMODE(before.st_mode) not in {0o700, 0o555}:
        raise _CandidateUnverifiedError("unexpected-mode")

    generation_entries: list[dict[str, Any]] = []
    snapshot_entries: list[dict[str, Any]] = []
    for name in sorted(expected_names):
        target = path / name
        try:
            file_stat = os.lstat(target)
        except OSError as exc:
            raise _CandidateUnverifiedError("identity-drift") from exc
        if _is_link_or_reparse(file_stat) or not stat.S_ISREG(file_stat.st_mode):
            raise _CandidateUnverifiedError("link-reparse-or-nonregular-entry")
        if file_stat.st_nlink != 1:
            raise _CandidateUnverifiedError("hard-linked-entry")
        if file_stat.st_size != 0:
            raise _CandidateUnverifiedError("nonzero-staging-bytes")
        if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) != 0o444:
            raise _CandidateUnverifiedError("unexpected-mode")
        _verify_empty_file_stable(target, file_stat)
        generation_entries.append(
            {
                "name": name,
                "object": _generation_file_record(file_stat),
                "sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
            }
        )
        snapshot_entries.append({"name": name, "object": list(_stat_record(file_stat))})

    after = _require_plain_directory(path, label="retrieval staging generation")
    if _stat_record(before) != _stat_record(after):
        raise _CandidateUnverifiedError("identity-drift")
    if set(_stable_directory_entries(path)) != expected_names:
        raise _CandidateUnverifiedError("concurrent-namespace-change")
    generation_identity = _digest_object(
        {
            "kind": RETRIEVAL_STAGING_KIND,
            "directory": _generation_directory_record(after),
            "entries": generation_entries,
        }
    )
    source_snapshot_identity = _digest_object(
        {
            "relative_path": relative_path,
            "directory": list(_stat_record(after)),
            "entries": snapshot_entries,
        }
    )
    return _CandidateSnapshot(
        kind=RETRIEVAL_STAGING_KIND,
        relative_path=relative_path,
        generation_identity=generation_identity,
        source_snapshot_identity=source_snapshot_identity,
        quarantine_name=_quarantine_name(RETRIEVAL_STAGING_KIND, generation_identity),
    )


def _snapshot_bridge_candidate(
    path: Path,
    *,
    relative_path: str,
    require_namespace_name: bool = True,
) -> _CandidateSnapshot:
    if require_namespace_name:
        suffix = path.name.removeprefix(BRIDGE_TEMP_PREFIX)
        if (
            not path.name.startswith(BRIDGE_TEMP_PREFIX)
            or _TEMP_SUFFIX_RE.fullmatch(suffix) is None
        ):
            raise _CandidateUnverifiedError("unsupported-namespace")
    try:
        before = _require_plain_directory(path, label="temporary bridge generation")
    except MillefeuilleContractError as exc:
        raise _CandidateUnverifiedError("link-reparse-or-nondirectory") from exc
    if os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o555:
        raise _CandidateUnverifiedError("writable-bridge-generation")

    entries = _stable_directory_entries(path)
    if BRIDGE_OWNERSHIP_MANIFEST not in entries:
        raise _CandidateUnverifiedError("missing-ownership-manifest")
    try:
        manifest_bytes = read_bytes_no_follow(
            path / BRIDGE_OWNERSHIP_MANIFEST,
            "temporary bridge ownership manifest",
            max_bytes=_BRIDGE_MANIFEST_MAX_BYTES,
        )
        manifest = _load_json_object(
            manifest_bytes,
            "temporary bridge ownership manifest",
        )
        _validate_bridge_manifest(manifest)
    except MillefeuilleContractError as exc:
        raise _CandidateUnverifiedError("invalid-ownership-manifest") from exc

    inventory = manifest["inventory"]
    expected_names = {
        BRIDGE_OWNERSHIP_MANIFEST,
        *(entry["relative_path"] for entry in inventory),
    }
    if set(entries) != expected_names:
        raise _CandidateUnverifiedError("inventory-drift", "unexpected-entries")

    generation_entries: list[dict[str, Any]] = []
    snapshot_entries: list[dict[str, Any]] = []
    for entry in inventory:
        name = entry["relative_path"]
        target = path / name
        try:
            file_stat = os.lstat(target)
        except OSError as exc:
            raise _CandidateUnverifiedError("inventory-drift") from exc
        if _is_link_or_reparse(file_stat) or not stat.S_ISREG(file_stat.st_mode):
            raise _CandidateUnverifiedError("link-reparse-or-nonregular-entry")
        if file_stat.st_nlink != 1:
            raise _CandidateUnverifiedError("hard-linked-entry")
        if file_stat.st_size != entry["size_bytes"]:
            raise _CandidateUnverifiedError("inventory-drift")
        if file_stat.st_mode & 0o222:
            raise _CandidateUnverifiedError("writable-bridge-generation")
        actual_digest, final_stat = _hash_regular_file_stable(target, file_stat)
        if not hmac.compare_digest(actual_digest, entry["sha256"]):
            raise _CandidateUnverifiedError("hash-drift")
        generation_entries.append(
            {
                "name": name,
                "object": _generation_file_record(final_stat),
                "sha256": actual_digest,
            }
        )
        snapshot_entries.append(
            {"name": name, "object": list(_stat_record(final_stat))}
        )

    manifest_stat = os.lstat(path / BRIDGE_OWNERSHIP_MANIFEST)
    if (
        _is_link_or_reparse(manifest_stat)
        or not stat.S_ISREG(manifest_stat.st_mode)
        or manifest_stat.st_nlink != 1
    ):
        raise _CandidateUnverifiedError("invalid-ownership-manifest")
    if manifest_stat.st_mode & 0o222:
        raise _CandidateUnverifiedError("writable-bridge-generation")
    manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    generation_entries.append(
        {
            "name": BRIDGE_OWNERSHIP_MANIFEST,
            "object": _generation_file_record(manifest_stat),
            "sha256": manifest_digest,
        }
    )
    snapshot_entries.append(
        {
            "name": BRIDGE_OWNERSHIP_MANIFEST,
            "object": list(_stat_record(manifest_stat)),
        }
    )

    after = _require_plain_directory(path, label="temporary bridge generation")
    if _stat_record(before) != _stat_record(after):
        raise _CandidateUnverifiedError("identity-drift")
    if set(_stable_directory_entries(path)) != expected_names:
        raise _CandidateUnverifiedError("concurrent-namespace-change")
    generation_identity = _digest_object(
        {
            "kind": TEMPORARY_BRIDGE_KIND,
            "directory": _generation_directory_record(after),
            "ownership_digest": manifest["integrity"]["content_digest"],
            "producer_id": manifest["producer_id"],
            "run_id": manifest["run_id"],
            "bridge_id": manifest["bridge_id"],
            "entries": sorted(generation_entries, key=lambda value: value["name"]),
        }
    )
    source_snapshot_identity = _digest_object(
        {
            "relative_path": relative_path,
            "directory": list(_stat_record(after)),
            "entries": sorted(snapshot_entries, key=lambda value: value["name"]),
        }
    )
    return _CandidateSnapshot(
        kind=TEMPORARY_BRIDGE_KIND,
        relative_path=relative_path,
        generation_identity=generation_identity,
        source_snapshot_identity=source_snapshot_identity,
        quarantine_name=_quarantine_name(TEMPORARY_BRIDGE_KIND, generation_identity),
    )


def _validate_bridge_manifest(payload: dict[str, Any]) -> None:
    _reject_secret_like(payload, "temporary bridge ownership manifest")
    _require_exact_fields(
        payload,
        _BRIDGE_FIELDS,
        "temporary bridge ownership manifest",
    )
    if payload.get("schema_version") != BRIDGE_OWNERSHIP_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "temporary bridge ownership schema_version is unsupported"
        )
    producer_id = _required_string(payload.get("producer_id"), "producer_id")
    if _SAFE_PRODUCER_RE.fullmatch(producer_id) is None:
        raise MillefeuilleContractError("producer_id is invalid")
    _safe_identity(_required_string(payload.get("run_id"), "run_id"), "run_id")
    _safe_identity(
        _required_string(payload.get("bridge_id"), "bridge_id"),
        "bridge_id",
    )
    if payload.get("publication_state") != "unpublished":
        raise MillefeuilleContractError(
            "temporary bridge publication_state must be unpublished"
        )
    if payload.get("cleanup_state") != "abandoned":
        raise MillefeuilleContractError(
            "temporary bridge cleanup_state must be abandoned"
        )
    raw_inventory = payload.get("inventory")
    if not isinstance(raw_inventory, list) or not 1 <= len(raw_inventory) <= 256:
        raise MillefeuilleContractError(
            "temporary bridge inventory must contain 1 through 256 entries"
        )
    normalized_paths: list[str] = []
    for index, raw_entry in enumerate(raw_inventory):
        if not isinstance(raw_entry, dict):
            raise MillefeuilleContractError(
                f"temporary bridge inventory[{index}] must be an object"
            )
        _require_exact_fields(
            raw_entry,
            _BRIDGE_INVENTORY_FIELDS,
            f"temporary bridge inventory[{index}]",
        )
        relative = _safe_flat_inventory_path(
            _required_string(
                raw_entry.get("relative_path"),
                f"inventory[{index}].relative_path",
                maximum=255,
            )
        )
        if relative == BRIDGE_OWNERSHIP_MANIFEST:
            raise MillefeuilleContractError(
                "temporary bridge inventory cannot include its ownership manifest"
            )
        if raw_entry.get("type") != "file":
            raise MillefeuilleContractError(
                "temporary bridge inventory supports regular files only"
            )
        _required_integer(
            raw_entry.get("size_bytes"),
            f"inventory[{index}].size_bytes",
            minimum=0,
        )
        digest = _required_string(
            raw_entry.get("sha256"),
            f"inventory[{index}].sha256",
            maximum=71,
        )
        if _HEX_DIGEST_RE.fullmatch(digest) is None:
            raise MillefeuilleContractError(
                "temporary bridge inventory sha256 is invalid"
            )
        normalized_paths.append(relative)
    if normalized_paths != sorted(set(normalized_paths)):
        raise MillefeuilleContractError(
            "temporary bridge inventory must be sorted and unique"
        )

    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise MillefeuilleContractError("temporary bridge integrity must be an object")
    _require_exact_fields(integrity, _INTEGRITY_FIELDS, "integrity")
    if integrity.get("algorithm") != "sha256":
        raise MillefeuilleContractError("temporary bridge integrity must use sha256")
    supplied_digest = _required_string(
        integrity.get("content_digest"),
        "integrity.content_digest",
        maximum=71,
    )
    if _HEX_DIGEST_RE.fullmatch(supplied_digest) is None:
        raise MillefeuilleContractError("temporary bridge content_digest is invalid")
    expected_digest = compute_bridge_ownership_digest(payload)
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise MillefeuilleContractError(
            "temporary bridge ownership content_digest mismatch"
        )


def _validate_plan_payload(payload: dict[str, Any]) -> None:
    _reject_secret_like(payload, "staging cleanup plan")
    _require_exact_fields(payload, _PLAN_FIELDS, "staging cleanup plan")
    if payload.get("schema_version") != STAGING_CLEANUP_PLAN_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "staging cleanup plan schema_version is unsupported"
        )
    _safe_identity(
        _required_string(payload.get("run_id"), "run_id", maximum=256),
        "run_id",
    )
    if payload.get("operation") != STAGING_CLEANUP_OPERATION:
        raise MillefeuilleContractError("staging cleanup operation is unsupported")
    roots = payload.get("roots")
    if not isinstance(roots, dict):
        raise MillefeuilleContractError("staging cleanup roots must be an object")
    _require_exact_fields(roots, _PLAN_ROOT_FIELDS, "staging cleanup roots")
    for key in sorted(_PLAN_ROOT_FIELDS):
        value = _required_string(roots.get(key), f"roots.{key}", maximum=71)
        if _HEX_DIGEST_RE.fullmatch(value) is None:
            raise MillefeuilleContractError("staging cleanup root identity is invalid")
    for field_name in (
        "source_namespace_identity",
        "quarantine_namespace_identity",
    ):
        value = _required_string(payload.get(field_name), field_name, maximum=71)
        if _HEX_DIGEST_RE.fullmatch(value) is None:
            raise MillefeuilleContractError(
                "staging cleanup namespace identity is invalid"
            )

    candidate_count = _required_integer(
        payload.get("candidate_count"),
        "candidate_count",
        minimum=1,
    )
    if candidate_count > 256:
        raise MillefeuilleContractError("candidate_count must not exceed 256")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != candidate_count:
        raise MillefeuilleContractError(
            "staging cleanup candidates do not match candidate_count"
        )
    ordering: list[tuple[str, str]] = []
    quarantine_names: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise MillefeuilleContractError(
                f"staging cleanup candidates[{index}] must be an object"
            )
        _require_exact_fields(
            candidate,
            _PLAN_CANDIDATE_FIELDS,
            f"staging cleanup candidates[{index}]",
        )
        kind = _required_string(candidate.get("kind"), f"candidates[{index}].kind")
        if kind not in {RETRIEVAL_STAGING_KIND, TEMPORARY_BRIDGE_KIND}:
            raise MillefeuilleContractError(
                "staging cleanup candidate kind is unsupported"
            )
        relative = _safe_relative_path(
            _required_string(
                candidate.get("relative_path"),
                f"candidates[{index}].relative_path",
                maximum=256,
            )
        )
        _require_kind_path_contract(kind, relative)
        generation_identity = _required_string(
            candidate.get("generation_identity"),
            f"candidates[{index}].generation_identity",
            maximum=71,
        )
        source_snapshot_identity = _required_string(
            candidate.get("source_snapshot_identity"),
            f"candidates[{index}].source_snapshot_identity",
            maximum=71,
        )
        if (
            _HEX_DIGEST_RE.fullmatch(generation_identity) is None
            or _HEX_DIGEST_RE.fullmatch(source_snapshot_identity) is None
        ):
            raise MillefeuilleContractError(
                "staging cleanup candidate identity is invalid"
            )
        quarantine_name = _required_string(
            candidate.get("quarantine_name"),
            f"candidates[{index}].quarantine_name",
            maximum=128,
        )
        expected_name = _quarantine_name(kind, generation_identity)
        if quarantine_name != expected_name:
            raise MillefeuilleContractError(
                "staging cleanup quarantine_name does not match generation identity"
            )
        if quarantine_name in quarantine_names:
            raise MillefeuilleContractError(
                "staging cleanup quarantine names must be unique"
            )
        quarantine_names.add(quarantine_name)
        ordering.append((kind, relative))
    if ordering != sorted(set(ordering)):
        raise MillefeuilleContractError(
            "staging cleanup candidates must be sorted and unique"
        )
    if payload.get("disposal") != STAGING_CLEANUP_DISPOSAL:
        raise MillefeuilleContractError("staging cleanup disposal is unsupported")
    raw_stops = payload.get("stop_conditions")
    if not isinstance(raw_stops, list) or tuple(raw_stops) != (
        STAGING_CLEANUP_STOP_CONDITIONS
    ):
        raise MillefeuilleContractError(
            "staging cleanup stop_conditions are unsupported"
        )
    if not all(isinstance(value, str) for value in raw_stops):
        raise MillefeuilleContractError(
            "staging cleanup stop_conditions must be strings"
        )
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise MillefeuilleContractError("staging cleanup integrity must be an object")
    _require_exact_fields(integrity, _INTEGRITY_FIELDS, "integrity")
    if integrity.get("algorithm") != "sha256":
        raise MillefeuilleContractError("staging cleanup integrity must use sha256")
    supplied_digest = _required_string(
        integrity.get("content_digest"),
        "integrity.content_digest",
        maximum=71,
    )
    if _HEX_DIGEST_RE.fullmatch(supplied_digest) is None:
        raise MillefeuilleContractError("staging cleanup plan digest is invalid")
    expected_digest = compute_staging_cleanup_plan_digest(payload)
    if not hmac.compare_digest(supplied_digest, expected_digest):
        raise MillefeuilleContractError("staging cleanup plan digest mismatch")


def _require_kind_path_contract(kind: str, relative_path: str) -> None:
    parts = PurePosixPath(relative_path).parts
    if kind == TEMPORARY_BRIDGE_KIND:
        if len(parts) != 1:
            raise MillefeuilleContractError(
                "temporary bridge candidate must be directly under the source root"
            )
        suffix = parts[0].removeprefix(BRIDGE_TEMP_PREFIX)
        if (
            not parts[0].startswith(BRIDGE_TEMP_PREFIX)
            or _TEMP_SUFFIX_RE.fullmatch(suffix) is None
        ):
            raise MillefeuilleContractError(
                "temporary bridge candidate has an unsupported namespace"
            )
        return
    expected_prefix = RETRIEVAL_BATCH_ROOT_REF.parts
    if (
        len(parts) != len(expected_prefix) + 2
        or parts[: len(expected_prefix)] != expected_prefix
        or _SAFE_BATCH_ID_RE.fullmatch(parts[-2]) is None
    ):
        raise MillefeuilleContractError(
            "retrieval staging candidate has an unsupported namespace"
        )
    suffix = parts[-1].removeprefix(_RETRIEVAL_BATCH_TEMP_PREFIX)
    if (
        not parts[-1].startswith(_RETRIEVAL_BATCH_TEMP_PREFIX)
        or _TEMP_SUFFIX_RE.fullmatch(suffix) is None
    ):
        raise MillefeuilleContractError(
            "retrieval staging candidate has an unsupported namespace"
        )


def apply_staging_cleanup_plan(
    *,
    plan_path: str | Path,
    source_root: str | Path,
    quarantine_root: str | Path,
    approval_receipt_path: str | Path,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Apply one exact cleanup plan after reserving MF-100 consumption."""

    plan = load_staging_cleanup_plan(plan_path)
    roots = _root_context(source_root, quarantine_root)
    _require_plan_roots(plan, roots)
    if roots.source_stat.st_dev != roots.quarantine_stat.st_dev:
        raise MillefeuilleContractError(
            "cleanup source and quarantine roots must be on the same filesystem"
        )
    if not _roots_support_atomic_quarantine(roots):
        raise MillefeuilleContractError(
            "cleanup requires stable same-device roots on a supported local "
            "filesystem with an atomic no-replace primitive"
        )
    receipt = load_approved_live_receipt(approval_receipt_path)
    request = _approved_live_request(plan, roots)
    audits = _load_cleanup_audits(roots.quarantine)
    states = _candidate_locations(plan, roots)

    if _all_already_quarantined(states):
        audit = _matching_cleanup_audit(
            audits,
            receipt=receipt,
            request=request,
            plan=plan,
        )
        return _build_disposition(
            status="already-quarantined",
            plan=plan,
            receipt_digest=receipt.content_digest,
            audit_identity=audit["integrity"]["content_digest"],
            candidates=tuple(
                (candidate, "already-quarantined") for candidate in plan["candidates"]
            ),
            failure_code=None,
        )
    if not _all_ready_to_quarantine(states):
        disposition = _build_disposition(
            status="recovery-required",
            plan=plan,
            receipt_digest=receipt.content_digest,
            audit_identity=_matching_audit_identity_or_none(
                audits,
                receipt.content_digest,
                plan["integrity"]["content_digest"],
            ),
            candidates=tuple(
                (candidate, state)
                for candidate, (state, _snapshot) in zip(
                    plan["candidates"],
                    states,
                    strict=True,
                )
            ),
            failure_code="partial-or-drifted-state",
        )
        raise CleanupApplyError(
            disposition,
            "cleanup state is partial or drifted; a separately approved recovery "
            "plan is required",
        )

    state = _scan(source_root=roots.source, quarantine_root=roots.quarantine)
    _require_plan_matches_scan(plan, state)
    replay_state, consumed_receipt_identities = _cleanup_replay_state(audits)
    if _receipt_identity(receipt.receipt_id) in consumed_receipt_identities:
        raise MillefeuilleContractError(
            "approved-live receipt identity has already been consumed"
        )
    evaluation_time = _normalize_evaluation_time(evaluated_at)
    validate_approved_live_receipt(
        receipt,
        request,
        now=evaluation_time,
        replay_state=replay_state,
    )

    moved: list[dict[str, Any]] = []
    lock_fd: int | None = None
    audit_record: dict[str, Any] | None = None
    transaction = ExitStack()
    pinned_roots: _PinnedRoots | None = None
    pinned_candidates: dict[str, _PinnedCandidate] = {}
    try:
        pinned_roots = transaction.enter_context(_pin_apply_roots(roots))
        lock_fd = _acquire_pinned_maintenance_lock(
            quarantine_fd=pinned_roots.quarantine_fd,
        )
        _require_pinned_roots_match_paths(roots, pinned_roots)
        locked_state = _scan(
            source_root=roots.source,
            quarantine_root=roots.quarantine,
            held_lock_fd=lock_fd,
            held_lock_parent_fd=pinned_roots.quarantine_fd,
        )
        _require_plan_matches_scan(
            plan,
            locked_state,
        )
        locked_audits = _load_cleanup_audits(roots.quarantine)
        replay_state, consumed_receipt_identities = _cleanup_replay_state(locked_audits)
        if _receipt_identity(receipt.receipt_id) in consumed_receipt_identities:
            raise MillefeuilleContractError(
                "approved-live receipt identity has already been consumed"
            )
        validate_approved_live_receipt(
            receipt,
            request,
            now=evaluation_time,
            replay_state=replay_state,
        )
        approved_audit = build_approved_live_audit_record(
            receipt,
            request,
            evaluated_at=evaluation_time,
            status="consumed",
            replay_state=replay_state,
        )
        audit_record = _build_cleanup_audit(
            approved_audit=approved_audit,
            receipt=receipt,
            plan=plan,
        )
        _reserve_cleanup_audit(
            roots.quarantine,
            audit_record,
            parent_fd=pinned_roots.quarantine_fd,
        )

        pinned_candidates = _pin_plan_candidates(
            transaction,
            roots=roots,
            pinned_roots=pinned_roots,
            plan=plan,
        )
        _acquire_pinned_retrieval_batch_locks(transaction, pinned_candidates)
        _require_pinned_roots_match_paths(roots, pinned_roots)
        final_state = _scan(
            source_root=roots.source,
            quarantine_root=roots.quarantine,
            held_lock_fd=lock_fd,
            held_lock_parent_fd=pinned_roots.quarantine_fd,
        )
        _require_pinned_roots_match_paths(roots, pinned_roots)
        _require_plan_matches_scan(
            plan,
            final_state,
            ignored_quarantine_names=frozenset(
                {
                    _cleanup_audit_name(receipt.content_digest),
                }
            ),
        )
        for candidate in plan["candidates"]:
            pinned = pinned_candidates[candidate["generation_identity"]]
            source = roots.source / Path(candidate["relative_path"])
            destination = roots.quarantine / candidate["quarantine_name"]
            current = _snapshot_candidate_at_source(source, candidate)
            _require_snapshot_matches_plan(current, candidate)
            _final_precommit_hook(candidate)
            _require_pinned_roots_match_paths(roots, pinned_roots)
            final_snapshot = _snapshot_candidate_at_source(source, candidate)
            _require_snapshot_matches_plan(final_snapshot, candidate)
            _require_pinned_candidate_ready(pinned, source)
            _validate_held_persistent_lock(
                pinned_roots.quarantine_fd,
                lock_fd,
            )
            if _relative_entry_exists(
                pinned_roots.quarantine_fd,
                candidate["quarantine_name"],
            ):
                raise MillefeuilleContractError(
                    "cleanup quarantine destination appeared concurrently"
                )
            with _pinned_generation_rename_window(pinned.generation_fd):
                _atomic_rename_noreplace(
                    source,
                    destination,
                    source_dir_fd=pinned.parent_fd,
                    destination_dir_fd=pinned_roots.quarantine_fd,
                    source_name=pinned.source_name,
                    destination_name=candidate["quarantine_name"],
                )
                moved.append(candidate)
            _fsync_directory_fd(pinned.parent_fd)
            _fsync_directory_fd(pinned_roots.quarantine_fd)
            _require_pinned_destination(pinned, pinned_roots.quarantine_fd)
            _require_pinned_roots_match_paths(roots, pinned_roots)
            destination_snapshot = _snapshot_candidate_at_destination(
                destination,
                candidate,
            )
            if not hmac.compare_digest(
                destination_snapshot.generation_identity,
                candidate["generation_identity"],
            ):
                raise MillefeuilleContractError(
                    "cleanup candidate identity changed during quarantine"
                )
        _require_pinned_roots_match_paths(roots, pinned_roots)
        _require_post_move_namespaces(
            before=state,
            roots=roots,
            plan=plan,
            receipt_digest=receipt.content_digest,
            held_lock_fd=lock_fd,
            held_lock_parent_fd=pinned_roots.quarantine_fd,
        )
        _require_pinned_roots_match_paths(roots, pinned_roots)
    except Exception as exc:
        if not moved:
            if audit_record is not None:
                disposition = _build_disposition(
                    status="failed-rolled-back",
                    plan=plan,
                    receipt_digest=receipt.content_digest,
                    audit_identity=audit_record["integrity"]["content_digest"],
                    candidates=tuple(
                        (candidate, "not-attempted") for candidate in plan["candidates"]
                    ),
                    failure_code="atomic-move-failed",
                )
                raise CleanupApplyError(
                    disposition,
                    "cleanup failed after receipt consumption; a separately "
                    "approved retry is required",
                ) from exc
            raise
        rollback_results, rollback_complete = _rollback_moved_candidates(
            roots=roots,
            moved=moved,
            pinned_roots=pinned_roots,
            pinned_candidates=pinned_candidates,
        )
        status = "failed-rolled-back" if rollback_complete else "recovery-required"
        failure_code = "atomic-move-failed" if rollback_complete else "rollback-blocked"
        candidate_statuses: list[tuple[dict[str, Any], str]] = []
        moved_status = {
            value["generation_identity"]: value["status"] for value in rollback_results
        }
        for candidate in plan["candidates"]:
            candidate_statuses.append(
                (
                    candidate,
                    moved_status.get(candidate["generation_identity"], "not-attempted"),
                )
            )
        disposition = _build_disposition(
            status=status,
            plan=plan,
            receipt_digest=receipt.content_digest,
            audit_identity=(
                audit_record["integrity"]["content_digest"]
                if audit_record is not None
                else None
            ),
            candidates=tuple(candidate_statuses),
            failure_code=failure_code,
        )
        raise CleanupApplyError(
            disposition,
            "cleanup move failed after receipt consumption; recovery requires a "
            "separate plan and receipt",
        ) from exc
    finally:
        if lock_fd is not None:
            _release_maintenance_lock(lock_fd)
        transaction.close()

    assert audit_record is not None
    return _build_disposition(
        status="quarantined",
        plan=plan,
        receipt_digest=receipt.content_digest,
        audit_identity=audit_record["integrity"]["content_digest"],
        candidates=tuple(
            (candidate, "quarantined") for candidate in plan["candidates"]
        ),
        failure_code=None,
    )


def _approved_live_request(
    plan: dict[str, Any],
    roots: _RootContext,
) -> ApprovedLiveRequest:
    targets = tuple(
        sorted(
            LiveTarget(
                kind=candidate["kind"],
                id=candidate["relative_path"],
            )
            for candidate in plan["candidates"]
        )
    )
    return ApprovedLiveRequest(
        run_id=plan["run_id"],
        operations=(STAGING_CLEANUP_OPERATION,),
        targets=targets,
        item_cap=plan["candidate_count"],
        selected_item_count=plan["candidate_count"],
        selector=LiveSelector(
            kind="maintenance-plan",
            value=plan["integrity"]["content_digest"],
        ),
        output_root=str(roots.quarantine),
        source_pack_root=str(roots.source),
        provider=None,
        provider_call_limit=0,
        cost_limit_usd_micros=0,
        disposal_policy=LiveDisposalPolicy(
            pdfs="not-applicable",
            provider_payloads="not-applicable",
            temporary_files=STAGING_CLEANUP_DISPOSAL,
        ),
        stop_conditions=tuple(plan["stop_conditions"]),
    )


def _final_precommit_hook(candidate: dict[str, Any]) -> None:
    """Test seam immediately before pinned final checks and atomic rename."""

    del candidate


def _candidate_locations(
    plan: dict[str, Any],
    roots: _RootContext,
) -> tuple[tuple[str, _CandidateSnapshot | None], ...]:
    results: list[tuple[str, _CandidateSnapshot | None]] = []
    for candidate in plan["candidates"]:
        source = roots.source / Path(candidate["relative_path"])
        destination = roots.quarantine / candidate["quarantine_name"]
        source_exists = _entry_exists_no_follow(source)
        destination_exists = _entry_exists_no_follow(destination)
        if source_exists and destination_exists:
            results.append(("source-and-quarantine-present", None))
            continue
        if source_exists:
            try:
                snapshot = _snapshot_candidate_at_source(source, candidate)
                _require_snapshot_matches_plan(snapshot, candidate)
            except (MillefeuilleContractError, _CandidateUnverifiedError):
                results.append(("source-drifted", None))
            else:
                results.append(("ready", snapshot))
            continue
        if destination_exists:
            try:
                snapshot = _snapshot_candidate_at_destination(
                    destination,
                    candidate,
                )
            except _CandidateUnverifiedError:
                results.append(("quarantine-drifted", None))
            else:
                if hmac.compare_digest(
                    snapshot.generation_identity,
                    candidate["generation_identity"],
                ):
                    results.append(("already-quarantined", snapshot))
                else:
                    results.append(("quarantine-drifted", None))
            continue
        results.append(("missing", None))
    return tuple(results)


def _snapshot_candidate_at_source(
    source: Path,
    candidate: dict[str, Any],
) -> _CandidateSnapshot:
    if candidate["kind"] == RETRIEVAL_STAGING_KIND:
        return _snapshot_retrieval_candidate(
            source,
            relative_path=candidate["relative_path"],
        )
    return _snapshot_bridge_candidate(
        source,
        relative_path=candidate["relative_path"],
    )


def _snapshot_candidate_at_destination(
    destination: Path,
    candidate: dict[str, Any],
) -> _CandidateSnapshot:
    if candidate["kind"] == RETRIEVAL_STAGING_KIND:
        return _snapshot_retrieval_candidate(
            destination,
            relative_path=candidate["relative_path"],
        )
    return _snapshot_bridge_candidate(
        destination,
        relative_path=candidate["relative_path"],
        require_namespace_name=False,
    )


def _require_snapshot_matches_plan(
    snapshot: _CandidateSnapshot,
    candidate: dict[str, Any],
) -> None:
    if (
        snapshot.kind != candidate["kind"]
        or snapshot.relative_path != candidate["relative_path"]
        or not hmac.compare_digest(
            snapshot.generation_identity,
            candidate["generation_identity"],
        )
        or not hmac.compare_digest(
            snapshot.source_snapshot_identity,
            candidate["source_snapshot_identity"],
        )
        or snapshot.quarantine_name != candidate["quarantine_name"]
    ):
        raise MillefeuilleContractError("cleanup candidate drifted from its plan")


def _all_already_quarantined(
    states: Sequence[tuple[str, _CandidateSnapshot | None]],
) -> bool:
    return bool(states) and all(state == "already-quarantined" for state, _ in states)


def _all_ready_to_quarantine(
    states: Sequence[tuple[str, _CandidateSnapshot | None]],
) -> bool:
    return bool(states) and all(state == "ready" for state, _ in states)


def _require_plan_roots(plan: dict[str, Any], roots: _RootContext) -> None:
    if not hmac.compare_digest(plan["roots"]["source"], roots.source_identity):
        raise MillefeuilleContractError("cleanup source root identity drifted")
    if not hmac.compare_digest(
        plan["roots"]["quarantine"],
        roots.quarantine_identity,
    ):
        raise MillefeuilleContractError("cleanup quarantine root identity drifted")


def _require_plan_matches_scan(
    plan: dict[str, Any],
    state: _ScanState,
    *,
    ignored_quarantine_names: frozenset[str] = frozenset(),
) -> None:
    _require_plan_roots(plan, state.roots)
    if not hmac.compare_digest(
        plan["source_namespace_identity"],
        state.source_namespace_identity,
    ):
        raise MillefeuilleContractError(
            "cleanup source namespace changed after planning"
        )
    quarantine_namespace = {
        name: record
        for name, record in state.quarantine_namespace.items()
        if name not in ignored_quarantine_names
    }
    if not hmac.compare_digest(
        plan["quarantine_namespace_identity"],
        _namespace_identity(quarantine_namespace),
    ):
        raise MillefeuilleContractError(
            "cleanup quarantine namespace changed after planning"
        )
    available = {candidate.relative_path: candidate for candidate in state.candidates}
    for expected in plan["candidates"]:
        actual = available.get(expected["relative_path"])
        if actual is None:
            raise MillefeuilleContractError(
                "cleanup candidate became unavailable after planning"
            )
        _require_snapshot_matches_plan(actual, expected)


def _build_cleanup_audit(
    *,
    approved_audit: dict[str, Any],
    receipt: ApprovedLiveReceipt,
    plan: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": STAGING_CLEANUP_AUDIT_SCHEMA_VERSION,
        "status": "consumed",
        "receipt_identity": _receipt_identity(receipt.receipt_id),
        "receipt_digest": receipt.content_digest,
        "request_digest": approved_audit["request_digest"],
        "plan_digest": plan["integrity"]["content_digest"],
        "operation": STAGING_CLEANUP_OPERATION,
        "candidate_count": plan["candidate_count"],
        "evaluated_at": approved_audit["evaluated_at"],
        "secret_material_persisted": False,
    }
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": _digest_object(payload),
    }
    _validate_cleanup_audit(payload)
    return payload


def _validate_cleanup_audit(payload: dict[str, Any]) -> None:
    _reject_secret_like(payload, "staging cleanup audit")
    _require_exact_fields(payload, _AUDIT_FIELDS, "staging cleanup audit")
    if payload.get("schema_version") != STAGING_CLEANUP_AUDIT_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "staging cleanup audit schema_version is unsupported"
        )
    if payload.get("status") != "consumed":
        raise MillefeuilleContractError("staging cleanup audit must be consumed")
    for field_name in (
        "receipt_identity",
        "receipt_digest",
        "request_digest",
        "plan_digest",
    ):
        value = _required_string(payload.get(field_name), field_name, maximum=71)
        if _HEX_DIGEST_RE.fullmatch(value) is None:
            raise MillefeuilleContractError("staging cleanup audit identity is invalid")
    if payload.get("operation") != STAGING_CLEANUP_OPERATION:
        raise MillefeuilleContractError("staging cleanup audit operation is invalid")
    count = _required_integer(
        payload.get("candidate_count"),
        "candidate_count",
        minimum=1,
    )
    if count > 256:
        raise MillefeuilleContractError(
            "staging cleanup audit candidate_count exceeds 256"
        )
    _parse_utc_timestamp(
        _required_string(payload.get("evaluated_at"), "evaluated_at", maximum=20)
    )
    if payload.get("secret_material_persisted") is not False:
        raise MillefeuilleContractError(
            "staging cleanup audit must not persist secret material"
        )
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise MillefeuilleContractError("staging cleanup audit integrity is invalid")
    _require_exact_fields(integrity, _INTEGRITY_FIELDS, "integrity")
    if integrity.get("algorithm") != "sha256":
        raise MillefeuilleContractError("staging cleanup audit integrity is invalid")
    supplied = _required_string(
        integrity.get("content_digest"),
        "integrity.content_digest",
        maximum=71,
    )
    if _HEX_DIGEST_RE.fullmatch(supplied) is None:
        raise MillefeuilleContractError("staging cleanup audit digest is invalid")
    body = dict(payload)
    body.pop("integrity", None)
    expected = _digest_object(body)
    if not hmac.compare_digest(supplied, expected):
        raise MillefeuilleContractError("staging cleanup audit digest mismatch")


def _load_cleanup_audits(quarantine_root: Path) -> tuple[dict[str, Any], ...]:
    audits: list[dict[str, Any]] = []
    entries = _stable_directory_entries(quarantine_root)
    for name in sorted(entries):
        if not name.startswith(_AUDIT_PREFIX):
            continue
        match = _AUDIT_NAME_RE.fullmatch(name)
        if match is None:
            raise MillefeuilleContractError(
                "quarantine contains an invalid reserved MF-106 audit entry"
            )
        payload_bytes = read_bytes_no_follow(
            quarantine_root / name,
            "staging cleanup audit",
            max_bytes=_CLEANUP_AUDIT_MAX_BYTES,
        )
        payload = _load_json_object(payload_bytes, "staging cleanup audit")
        _validate_cleanup_audit(payload)
        if payload["receipt_digest"].removeprefix("sha256:") != match.group(1):
            raise MillefeuilleContractError(
                "staging cleanup audit filename does not match receipt digest"
            )
        audits.append(payload)
    return tuple(audits)


def _cleanup_replay_state(
    audits: Collection[dict[str, Any]],
) -> tuple[ReceiptReplayState, frozenset[str]]:
    return (
        ReceiptReplayState(
            receipt_ids=frozenset(),
            content_digests=frozenset(audit["receipt_digest"] for audit in audits),
        ),
        frozenset(audit["receipt_identity"] for audit in audits),
    )


def _matching_cleanup_audit(
    audits: Collection[dict[str, Any]],
    *,
    receipt: ApprovedLiveReceipt,
    request: ApprovedLiveRequest,
    plan: dict[str, Any],
) -> dict[str, Any]:
    match = next(
        (
            audit
            for audit in audits
            if audit["receipt_digest"] == receipt.content_digest
            and audit["receipt_identity"] == _receipt_identity(receipt.receipt_id)
            and audit["plan_digest"] == plan["integrity"]["content_digest"]
        ),
        None,
    )
    if match is None:
        raise MillefeuilleContractError(
            "already-quarantined state has no matching consumed audit"
        )
    # An already-complete rerun has no effect.  Validate exact scope at the
    # original approval boundary without treating replay or later expiry as a
    # reason to mutate anything.
    approved_at = _parse_utc_timestamp(receipt.approval.approved_at)
    validate_approved_live_receipt_for_no_effect(receipt, request, now=approved_at)
    expected = build_approved_live_audit_record(
        receipt,
        request,
        evaluated_at=approved_at,
        status="validated",
        replay_state=ReceiptReplayState(),
    )
    if not hmac.compare_digest(match["request_digest"], expected["request_digest"]):
        raise MillefeuilleContractError(
            "already-quarantined audit request identity drifted"
        )
    return match


def _matching_audit_identity_or_none(
    audits: Collection[dict[str, Any]],
    receipt_digest: str,
    plan_digest: str,
) -> str | None:
    for audit in audits:
        if (
            audit["receipt_digest"] == receipt_digest
            and audit["plan_digest"] == plan_digest
        ):
            return audit["integrity"]["content_digest"]
    return None


def _reserve_cleanup_audit(
    quarantine_root: Path,
    audit: dict[str, Any],
    *,
    parent_fd: int | None = None,
) -> None:
    target = quarantine_root / _cleanup_audit_name(audit["receipt_digest"])
    payload = (
        json.dumps(
            audit,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_exclusive_regular_file(
        target,
        payload,
        mode=0o600,
        parent_fd=parent_fd,
    )


def _build_disposition(
    *,
    status: str,
    plan: dict[str, Any],
    receipt_digest: str,
    audit_identity: str | None,
    candidates: Sequence[tuple[dict[str, Any], str]],
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": STAGING_CLEANUP_DISPOSITION_SCHEMA_VERSION,
        "status": status,
        "plan_digest": plan["integrity"]["content_digest"],
        "operation": STAGING_CLEANUP_OPERATION,
        "receipt_digest": receipt_digest,
        "audit_identity": audit_identity,
        "candidate_count": plan["candidate_count"],
        "candidates": [
            {
                "kind": candidate["kind"],
                "generation_identity": candidate["generation_identity"],
                "status": candidate_status,
            }
            for candidate, candidate_status in candidates
        ],
        "failure_code": failure_code,
        "permanent_deletion_performed": False,
        "private_bytes_serialized": False,
        "secret_material_persisted": False,
    }


@contextmanager
def _pin_apply_roots(roots: _RootContext) -> Iterator[_PinnedRoots]:
    source_fd = _open_plain_directory_fd(roots.source)
    quarantine_fd: int | None = None
    try:
        quarantine_fd = _open_plain_directory_fd(roots.quarantine)
        pinned = _PinnedRoots(source_fd=source_fd, quarantine_fd=quarantine_fd)
        _require_pinned_roots_match_paths(roots, pinned)
        yield pinned
    finally:
        if quarantine_fd is not None:
            os.close(quarantine_fd)
        os.close(source_fd)


def _open_plain_directory_fd(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup could not pin a required directory"
        ) from exc
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or _is_link_or_reparse(value):
        os.close(fd)
        raise MillefeuilleContractError("cleanup pinned directory is unsafe")
    return fd


def _open_relative_plain_directory_fd(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup could not pin a descendant directory"
        ) from exc
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or _is_link_or_reparse(value):
        os.close(fd)
        raise MillefeuilleContractError("cleanup pinned descendant directory is unsafe")
    return fd


def _require_pinned_roots_match_paths(
    roots: _RootContext,
    pinned: _PinnedRoots,
) -> None:
    try:
        source_named = os.lstat(roots.source)
        quarantine_named = os.lstat(roots.quarantine)
        source_opened = os.fstat(pinned.source_fd)
        quarantine_opened = os.fstat(pinned.quarantine_fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup root path changed while pinned"
        ) from exc
    if (
        _object_identity(source_named) != _object_identity(source_opened)
        or _object_identity(quarantine_named) != _object_identity(quarantine_opened)
        or _object_identity(source_opened) != _object_identity(roots.source_stat)
        or _object_identity(quarantine_opened)
        != _object_identity(roots.quarantine_stat)
    ):
        raise MillefeuilleContractError("cleanup root path changed while pinned")


def _acquire_pinned_maintenance_lock(
    *,
    quarantine_fd: int,
) -> int:
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(_LOCK_NAME, flags, dir_fd=quarantine_fd)
    except FileNotFoundError:
        create_flags = flags | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(_LOCK_NAME, create_flags, 0o600, dir_fd=quarantine_fd)
        except FileExistsError:
            try:
                fd = os.open(_LOCK_NAME, flags, dir_fd=quarantine_fd)
            except OSError as exc:
                raise MillefeuilleContractError(
                    "cleanup could not open its persistent maintenance lock"
                ) from exc
        except OSError as exc:
            raise MillefeuilleContractError(
                "cleanup could not initialize its persistent lock"
            ) from exc
        else:
            try:
                if os.write(fd, b"0") != 1:
                    raise MillefeuilleContractError(
                        "cleanup could not initialize its persistent lock"
                    )
                os.fsync(fd)
                _fsync_directory_fd(quarantine_fd)
            except Exception as exc:
                # The reserved name is persistent by design. A failed
                # initialization is left in place as a fail-closed malformed
                # lock and is never unlinked automatically.
                os.close(fd)
                if isinstance(exc, MillefeuilleContractError):
                    raise
                raise MillefeuilleContractError(
                    "cleanup could not initialize its persistent lock"
                ) from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup could not open its persistent maintenance lock"
        ) from exc
    try:
        opened = os.fstat(fd)
        named = os.stat(_LOCK_NAME, dir_fd=quarantine_fd, follow_symlinks=False)
        if (
            _object_identity(opened) != _object_identity(named)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != 1
        ):
            raise MillefeuilleContractError(
                "cleanup persistent maintenance lock is malformed"
            )
        os.lseek(fd, 0, os.SEEK_SET)
        if os.read(fd, 2) != b"0":
            raise MillefeuilleContractError(
                "cleanup persistent maintenance lock is malformed"
            )
        os.lseek(fd, 0, os.SEEK_SET)
        _lock_file_descriptor(fd)
        return fd
    except Exception:
        os.close(fd)
        raise


def _pin_plan_candidates(
    stack: ExitStack,
    *,
    roots: _RootContext,
    pinned_roots: _PinnedRoots,
    plan: dict[str, Any],
) -> dict[str, _PinnedCandidate]:
    pinned: dict[str, _PinnedCandidate] = {}
    for candidate in plan["candidates"]:
        parts = PurePosixPath(candidate["relative_path"]).parts
        parent_fd = os.dup(pinned_roots.source_fd)
        stack.callback(os.close, parent_fd)
        for part in parts[:-1]:
            next_fd = _open_relative_plain_directory_fd(parent_fd, part)
            stack.callback(os.close, next_fd)
            parent_fd = next_fd
        generation_fd = _open_relative_plain_directory_fd(parent_fd, parts[-1])
        stack.callback(os.close, generation_fd)
        value = _PinnedCandidate(
            candidate=candidate,
            parent_fd=parent_fd,
            generation_fd=generation_fd,
            source_name=parts[-1],
        )
        source = roots.source / Path(candidate["relative_path"])
        _require_pinned_candidate_ready(value, source)
        pinned[candidate["generation_identity"]] = value
    return pinned


def _require_pinned_candidate_ready(
    pinned: _PinnedCandidate,
    source_path: Path,
) -> None:
    try:
        parent_named = os.lstat(source_path.parent)
        parent_opened = os.fstat(pinned.parent_fd)
        source_named = os.stat(
            pinned.source_name,
            dir_fd=pinned.parent_fd,
            follow_symlinks=False,
        )
        source_opened = os.fstat(pinned.generation_fd)
        absolute_named = os.lstat(source_path)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup candidate parent or name changed while pinned"
        ) from exc
    if (
        _object_identity(parent_named) != _object_identity(parent_opened)
        or _object_identity(source_named) != _object_identity(source_opened)
        or _object_identity(absolute_named) != _object_identity(source_opened)
        or not stat.S_ISDIR(source_opened.st_mode)
    ):
        raise MillefeuilleContractError(
            "cleanup candidate parent or name changed while pinned"
        )


def _require_pinned_destination(
    pinned: _PinnedCandidate,
    quarantine_fd: int,
) -> None:
    destination_name = pinned.candidate["quarantine_name"]
    try:
        destination = os.stat(
            destination_name,
            dir_fd=quarantine_fd,
            follow_symlinks=False,
        )
        opened = os.fstat(pinned.generation_fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup quarantine destination changed after rename"
        ) from exc
    if _object_identity(destination) != _object_identity(opened):
        raise MillefeuilleContractError(
            "cleanup quarantine destination changed after rename"
        )
    if _relative_entry_exists(pinned.parent_fd, pinned.source_name):
        raise MillefeuilleContractError(
            "cleanup source name remained after quarantine rename"
        )


def _relative_entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup relative namespace could not be inspected"
        ) from exc
    return True


def _acquire_pinned_retrieval_batch_locks(
    stack: ExitStack,
    pinned_candidates: dict[str, _PinnedCandidate],
) -> None:
    seen: set[tuple[int, int]] = set()
    for pinned in pinned_candidates.values():
        if pinned.candidate["kind"] != RETRIEVAL_STAGING_KIND:
            continue
        parent = os.fstat(pinned.parent_fd)
        identity = (parent.st_dev, parent.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        stack.enter_context(_exclusive_pinned_directory_lock(pinned.parent_fd))


@contextmanager
def _exclusive_pinned_directory_lock(parent_fd: int) -> Iterator[None]:
    try:
        import fcntl

        fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, ImportError, OSError) as exc:
        raise MillefeuilleContractError(
            "cleanup found an active or unavailable pinned retrieval batch lock"
        ) from exc
    try:
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(parent_fd, fcntl.LOCK_UN)


def _release_maintenance_lock(lock_fd: int) -> None:
    try:
        _unlock_file_descriptor(lock_fd)
    finally:
        os.close(lock_fd)


def _validate_persistent_lock(path: Path) -> None:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock is unavailable"
        ) from exc
    if (
        _is_link_or_reparse(value)
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or value.st_size != 1
    ):
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock is malformed"
        )
    try:
        payload = read_bytes_no_follow(
            path,
            "cleanup persistent maintenance lock",
            max_bytes=1,
        )
    except MillefeuilleContractError as exc:
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock is malformed"
        ) from exc
    if payload != b"0":
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock is malformed"
        )


def _lock_file_descriptor(fd: int) -> None:
    if os.name == "posix":
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise MillefeuilleContractError(
                "cleanup found an active maintenance lock"
            ) from exc
        return
    if os.name == "nt":
        try:
            import msvcrt

            # Lock one byte immediately beyond the one-byte public marker so
            # concurrent validation can still read the marker on Windows.
            os.lseek(fd, 1, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise MillefeuilleContractError(
                "cleanup found an active maintenance lock"
            ) from exc
        return
    raise MillefeuilleContractError(
        "cleanup advisory maintenance locks are unsupported"
    )


def _unlock_file_descriptor(fd: int) -> None:
    if os.name == "posix":
        import fcntl

        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        with suppress(OSError):
            os.lseek(fd, 1, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _rollback_moved_candidates(
    *,
    roots: _RootContext,
    moved: Sequence[dict[str, Any]],
    pinned_roots: _PinnedRoots | None,
    pinned_candidates: dict[str, _PinnedCandidate],
) -> tuple[tuple[dict[str, str], ...], bool]:
    results: list[dict[str, str]] = []
    complete = True
    for candidate in reversed(moved):
        source = roots.source / Path(candidate["relative_path"])
        destination = roots.quarantine / candidate["quarantine_name"]
        status = "rollback-blocked"
        try:
            if pinned_roots is None:
                raise MillefeuilleContractError(
                    "cleanup rollback has no pinned root descriptors"
                )
            pinned = pinned_candidates[candidate["generation_identity"]]
            if _relative_entry_exists(pinned.parent_fd, pinned.source_name):
                raise MillefeuilleContractError(
                    "cleanup rollback source appeared concurrently"
                )
            _require_pinned_destination(pinned, pinned_roots.quarantine_fd)
            with _pinned_generation_rename_window(pinned.generation_fd):
                _atomic_rename_noreplace(
                    destination,
                    source,
                    source_dir_fd=pinned_roots.quarantine_fd,
                    destination_dir_fd=pinned.parent_fd,
                    source_name=candidate["quarantine_name"],
                    destination_name=pinned.source_name,
                )
            _fsync_directory_fd(pinned_roots.quarantine_fd)
            _fsync_directory_fd(pinned.parent_fd)
            restored_named = os.stat(
                pinned.source_name,
                dir_fd=pinned.parent_fd,
                follow_symlinks=False,
            )
            restored_opened = os.fstat(pinned.generation_fd)
            if _object_identity(restored_named) != _object_identity(restored_opened):
                raise MillefeuilleContractError(
                    "cleanup rollback restored a drifted generation"
                )
            _require_pinned_candidate_ready(pinned, source)
            restored_snapshot = _snapshot_candidate_at_source(source, candidate)
            if (
                restored_snapshot.kind != candidate["kind"]
                or restored_snapshot.relative_path != candidate["relative_path"]
                or not hmac.compare_digest(
                    restored_snapshot.generation_identity,
                    candidate["generation_identity"],
                )
                or restored_snapshot.quarantine_name != candidate["quarantine_name"]
            ):
                raise MillefeuilleContractError(
                    "cleanup rollback restored a drifted generation"
                )
            status = "rolled-back"
        except Exception:
            complete = False
        results.append(
            {
                "generation_identity": candidate["generation_identity"],
                "status": status,
            }
        )
    return tuple(results), complete


def _require_post_move_namespaces(
    *,
    before: _ScanState,
    roots: _RootContext,
    plan: dict[str, Any],
    receipt_digest: str,
    held_lock_fd: int,
    held_lock_parent_fd: int,
) -> None:
    after_source = _capture_source_namespace(roots.source)
    removed_keys = {
        _source_namespace_key(candidate) for candidate in plan["candidates"]
    }
    changed_batch_keys = {
        f"retrieval-batch:{PurePosixPath(candidate['relative_path']).parts[-2]}"
        for candidate in plan["candidates"]
        if candidate["kind"] == RETRIEVAL_STAGING_KIND
    }
    expected_source = {
        key: record
        for key, record in before.source_namespace.items()
        if key not in removed_keys and key not in changed_batch_keys
    }
    comparable_after_source = {
        key: record
        for key, record in after_source.items()
        if key not in changed_batch_keys
    }
    if comparable_after_source != expected_source:
        raise MillefeuilleContractError(
            "cleanup source namespace changed during quarantine"
        )

    after_quarantine = _cleanup_quarantine_namespace(
        roots.quarantine,
        held_lock_fd=held_lock_fd,
        held_lock_parent_fd=held_lock_parent_fd,
    )
    expected_new_names = {
        _cleanup_audit_name(receipt_digest),
        *(candidate["quarantine_name"] for candidate in plan["candidates"]),
    }
    if set(after_quarantine) != set(before.quarantine_namespace) | expected_new_names:
        raise MillefeuilleContractError(
            "cleanup quarantine namespace changed during quarantine"
        )
    for name, record in before.quarantine_namespace.items():
        if after_quarantine.get(name) != record:
            raise MillefeuilleContractError(
                "cleanup quarantine namespace entry drifted"
            )


def _source_namespace_key(candidate: dict[str, Any]) -> str:
    if candidate["kind"] == TEMPORARY_BRIDGE_KIND:
        return f"bridge:{candidate['relative_path']}"
    parts = PurePosixPath(candidate["relative_path"]).parts
    return f"retrieval:{parts[-2]}/{parts[-1]}"


def _capture_source_namespace(source_root: Path) -> dict[str, tuple[str, ...]]:
    # Reuse the same scanner without requiring a second quarantine root.
    namespace: dict[str, tuple[str, ...]] = {}
    for name, record in _stable_directory_entries(source_root).items():
        if name.startswith(BRIDGE_TEMP_PREFIX):
            namespace[f"bridge:{name}"] = record
    batch_root = source_root / RETRIEVAL_BATCH_ROOT_REF
    if not _entry_exists_no_follow(batch_root):
        return namespace
    try:
        batch_root_stat = _require_plain_directory(
            batch_root,
            label="retrieval batch namespace",
        )
    except MillefeuilleContractError:
        return namespace
    namespace["retrieval-root"] = _stat_record(batch_root_stat)
    for batch_name, batch_record in _stable_directory_entries(batch_root).items():
        namespace[f"retrieval-batch:{batch_name}"] = batch_record
        if _SAFE_BATCH_ID_RE.fullmatch(batch_name) is None:
            continue
        batch_path = batch_root / batch_name
        try:
            _require_plain_directory(batch_path, label="retrieval batch directory")
            entries = _stable_directory_entries(batch_path)
        except MillefeuilleContractError:
            continue
        for name, record in entries.items():
            namespace[f"retrieval:{batch_name}/{name}"] = record
    return namespace


def _cleanup_quarantine_namespace(
    quarantine_root: Path,
    *,
    held_lock_fd: int | None = None,
    held_lock_parent_fd: int | None = None,
) -> dict[str, tuple[str, ...]]:
    entries = _stable_directory_entries(quarantine_root)
    if held_lock_fd is not None:
        if held_lock_parent_fd is None:
            raise MillefeuilleContractError(
                "cleanup held lock requires its pinned quarantine root"
            )
        # A held lock is part of every locked scan, even if an absolute-path
        # listing races with its name being removed or the root being swapped.
        # Resolve the reserved name through the pinned root and require the
        # listing to have observed that same required namespace entry.
        _validate_held_persistent_lock(
            held_lock_parent_fd,
            held_lock_fd,
        )
        if _LOCK_NAME not in entries:
            raise MillefeuilleContractError(
                "cleanup persistent maintenance lock changed while held"
            )
        entries = dict(entries)
        entries.pop(_LOCK_NAME)
    elif _LOCK_NAME in entries:
        _validate_persistent_lock(quarantine_root / _LOCK_NAME)
        entries = dict(entries)
        entries.pop(_LOCK_NAME)
    return entries


def _validate_held_persistent_lock(quarantine_fd: int, fd: int) -> None:
    try:
        opened_before = os.fstat(fd)
        named = os.stat(
            _LOCK_NAME,
            dir_fd=quarantine_fd,
            follow_symlinks=False,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        payload = os.read(fd, 2)
        opened_after = os.fstat(fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock changed while held"
        ) from exc
    if (
        _object_identity(opened_before) != _object_identity(named)
        or _stat_record(opened_before) != _stat_record(opened_after)
        or not stat.S_ISREG(opened_after.st_mode)
        or opened_after.st_nlink != 1
        or opened_after.st_size != 1
        or payload != b"0"
    ):
        raise MillefeuilleContractError(
            "cleanup persistent maintenance lock changed while held"
        )


@contextmanager
def _pinned_generation_rename_window(generation_fd: int) -> Iterator[None]:
    """Make an immutable generation renameable without weakening its contents.

    OverlayFS requires owner write and execute permission on a directory inode
    before it can copy the inode up for a rename, even when both parent
    directories are writable.  Bridge generations are deliberately mode 0555,
    so open the smallest possible mutation window through the already-pinned
    descriptor and restore the exact mode on either side of the atomic rename.
    """

    try:
        before = os.fstat(generation_fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup pinned generation could not be inspected before rename"
        ) from exc
    if not stat.S_ISDIR(before.st_mode):
        raise MillefeuilleContractError(
            "cleanup pinned generation changed before rename"
        )
    original_mode = stat.S_IMODE(before.st_mode)
    rename_mode = original_mode | stat.S_IWUSR | stat.S_IXUSR
    changed = rename_mode != original_mode
    if changed:
        try:
            os.fchmod(generation_fd, rename_mode)
            opened = os.fstat(generation_fd)
        except OSError as exc:
            raise MillefeuilleContractError(
                "cleanup pinned generation mode could not be managed for rename"
            ) from exc
        try:
            if (
                _object_identity(opened) != _object_identity(before)
                or stat.S_IMODE(opened.st_mode) != rename_mode
            ):
                raise MillefeuilleContractError(
                    "cleanup pinned generation changed while preparing rename"
                )
        except Exception:
            with suppress(OSError):
                os.fchmod(generation_fd, original_mode)
            raise
    try:
        yield
    finally:
        if changed:
            try:
                os.fchmod(generation_fd, original_mode)
                restored = os.fstat(generation_fd)
            except OSError as exc:
                raise MillefeuilleContractError(
                    "cleanup pinned generation mode could not be restored"
                ) from exc
            if (
                _object_identity(restored) != _object_identity(before)
                or stat.S_IMODE(restored.st_mode) != original_mode
            ):
                raise MillefeuilleContractError(
                    "cleanup pinned generation mode was not restored"
                )


def _atomic_rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
    source_name: str | None = None,
    destination_name: str | None = None,
) -> None:
    if os.name != "posix":
        raise MillefeuilleContractError(
            "cleanup atomic no-replace rename is unsupported on this platform"
        )
    if sys.platform == "darwin" and (
        source_dir_fd is not None or destination_dir_fd is not None
    ):
        raise MillefeuilleContractError(
            "cleanup pinned atomic quarantine is not supported on macOS"
        )
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            if _entry_exists_no_follow(destination):
                raise FileExistsError(str(destination))
            rename_noreplace = libc.renamex_np
            no_replace_flag = 0x00000004
            rename_noreplace.argtypes = (
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_noreplace.restype = ctypes.c_int
            result = rename_noreplace(
                os.fsencode(source),
                os.fsencode(destination),
                no_replace_flag,
            )
        else:
            if source_dir_fd is None:
                source_dir_fd = -100
                source_name = os.fspath(source)
            if destination_dir_fd is None:
                destination_dir_fd = -100
                destination_name = os.fspath(destination)
            assert source_name is not None
            assert destination_name is not None
            if _relative_entry_exists(destination_dir_fd, destination_name):
                raise FileExistsError(str(destination))
            rename_noreplace = libc.renameat2
            no_replace_flag = 1
            rename_noreplace.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_noreplace.restype = ctypes.c_int
            result = rename_noreplace(
                source_dir_fd,
                os.fsencode(source_name),
                destination_dir_fd,
                os.fsencode(destination_name),
                no_replace_flag,
            )
    except (AttributeError, ImportError, OSError) as exc:
        raise MillefeuilleContractError(
            "cleanup requires an atomic no-replace quarantine primitive"
        ) from exc
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(str(destination))
    if error_number == errno.EXDEV:
        raise MillefeuilleContractError("cleanup quarantine move crossed filesystems")
    unsupported = {errno.EINVAL, errno.ENOSYS}
    for name in ("ENOTSUP", "EOPNOTSUPP"):
        value = getattr(errno, name, None)
        if value is not None:
            unsupported.add(value)
    if error_number in unsupported:
        raise MillefeuilleContractError(
            "cleanup requires an atomic no-replace quarantine primitive"
        )
    raise OSError(error_number, os.strerror(error_number))


def _has_atomic_no_replace_rename() -> bool:
    if os.name != "posix":
        return False
    try:
        import ctypes

        library = ctypes.CDLL(None, use_errno=True)
        symbol = "renamex_np" if sys.platform == "darwin" else "renameat2"
        getattr(library, symbol)
    except (AttributeError, ImportError, OSError):
        return False
    return True


def _roots_support_atomic_quarantine(roots: _RootContext) -> bool:
    # Mutation additionally requires descriptor-relative pinned parents.  The
    # v0.1 executor supports that boundary on Linux only; Windows and macOS
    # remain portable inspect/plan platforms and refuse before reservation.
    return (
        roots.source_stat.st_dev == roots.quarantine_stat.st_dev
        and roots.source_stat.st_ino != 0
        and roots.quarantine_stat.st_ino != 0
        and _has_pinned_directory_primitives()
    )


def _has_pinned_directory_primitives() -> bool:
    """Return whether this runtime exposes the complete Linux mutation boundary."""

    return (
        os.name == "posix"
        and sys.platform.startswith("linux")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and _has_posix_batch_lock()
        and _has_atomic_no_replace_rename()
    )


def _has_posix_batch_lock() -> bool:
    if os.name != "posix":
        return False
    try:
        import fcntl  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def _write_exclusive_regular_file(
    path: Path,
    payload: bytes,
    *,
    mode: int,
    parent_fd: int | None = None,
) -> None:
    parent_before = _require_plain_directory(path.parent, label="cleanup audit root")
    if parent_fd is not None:
        pinned_parent = os.fstat(parent_fd)
        if _object_identity(parent_before) != _object_identity(pinned_parent):
            raise MillefeuilleContractError(
                "cleanup audit root changed before reservation"
            )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOINHERIT"):
        flags |= os.O_NOINHERIT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if parent_fd is None:
            fd = os.open(path, flags, mode)
        else:
            fd = os.open(path.name, flags, mode, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise MillefeuilleContractError(
            "approved-live cleanup audit reservation already exists"
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ELOOP}:
            raise MillefeuilleContractError(
                "approved-live cleanup audit reservation already exists"
            ) from exc
        raise MillefeuilleContractError(
            "could not reserve approved-live cleanup audit"
        ) from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise MillefeuilleContractError(
                "approved-live cleanup audit reservation is unsafe"
            )
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise MillefeuilleContractError(
                    "could not write complete approved-live cleanup audit"
                )
            offset += written
        os.fsync(fd)
        final_fd = os.fstat(fd)
        final_named = (
            os.lstat(path)
            if parent_fd is None
            else os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        )
        parent_after = _require_plain_directory(path.parent, label="cleanup audit root")
        final_parent = os.fstat(parent_fd) if parent_fd is not None else parent_after
        if (
            _object_identity(final_fd) != _object_identity(final_named)
            or final_fd.st_nlink != 1
            or final_fd.st_size != len(payload)
            or _object_identity(parent_before) != _object_identity(parent_after)
            or _object_identity(parent_after) != _object_identity(final_parent)
        ):
            raise MillefeuilleContractError(
                "approved-live cleanup audit changed while reserved"
            )
        if parent_fd is None:
            _fsync_directory(path.parent)
        else:
            _fsync_directory_fd(parent_fd)
    except Exception:
        # A successfully created reservation remains consumed even when its
        # durability or post-write verification reports a failure.  Never
        # delete it automatically or silently reuse the receipt.
        raise
    finally:
        os.close(fd)


def _hash_regular_file_stable(
    path: Path,
    expected_stat: os.stat_result,
) -> tuple[str, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOINHERIT"):
        flags |= os.O_NOINHERIT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _CandidateUnverifiedError("identity-drift") from exc
    try:
        opened = os.fstat(fd)
        if (
            _object_identity(opened) != _object_identity(expected_stat)
            or _stat_record(opened) != _stat_record(expected_stat)
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise _CandidateUnverifiedError("identity-drift")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_stat.st_size:
                raise _CandidateUnverifiedError("identity-drift")
            digest.update(chunk)
        final_fd = os.fstat(fd)
        final_named = os.lstat(path)
        if (
            total != expected_stat.st_size
            or _stat_record(final_fd) != _stat_record(expected_stat)
            or _stat_record(final_named) != _stat_record(expected_stat)
        ):
            raise _CandidateUnverifiedError("identity-drift")
        return "sha256:" + digest.hexdigest(), final_fd
    finally:
        os.close(fd)


def _verify_empty_file_stable(path: Path, expected_stat: os.stat_result) -> None:
    actual_digest, final_stat = _hash_regular_file_stable(path, expected_stat)
    if (
        actual_digest != "sha256:" + hashlib.sha256(b"").hexdigest()
        or final_stat.st_size != 0
    ):
        raise _CandidateUnverifiedError("nonzero-staging-bytes")


def _stable_directory_entries(path: Path) -> dict[str, tuple[str, ...]]:
    before = _require_plain_directory(path, label="cleanup directory")
    try:
        with os.scandir(path) as iterator:
            names = sorted(entry.name for entry in iterator)
        entries = {name: _safe_stat_record(path / name) for name in names}
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup directory could not be inspected safely"
        ) from exc
    after = _require_plain_directory(path, label="cleanup directory")
    if _stat_record(before) != _stat_record(after):
        raise MillefeuilleContractError(
            "cleanup directory changed while being inspected"
        )
    try:
        with os.scandir(path) as iterator:
            final_names = sorted(entry.name for entry in iterator)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup directory could not be revalidated"
        ) from exc
    if names != final_names:
        raise MillefeuilleContractError(
            "cleanup directory namespace changed while being inspected"
        )
    return entries


def _safe_stat_record(path: Path) -> tuple[str, ...]:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup namespace entry changed while being inspected"
        ) from exc
    return _stat_record(value)


def _stat_record(value: os.stat_result) -> tuple[str, ...]:
    ctime = "not-recorded" if os.name == "nt" else str(value.st_ctime_ns)
    return (
        str(value.st_dev),
        str(value.st_ino),
        str(stat.S_IFMT(value.st_mode)),
        str(stat.S_IMODE(value.st_mode)),
        str(value.st_nlink),
        str(value.st_size),
        str(value.st_mtime_ns),
        ctime,
        "reparse" if _is_link_or_reparse(value) else "plain",
    )


def _generation_directory_record(value: os.stat_result) -> list[str]:
    return [
        str(value.st_dev),
        str(value.st_ino),
        str(stat.S_IFMT(value.st_mode)),
        str(stat.S_IMODE(value.st_mode)),
        str(value.st_nlink),
    ]


def _generation_file_record(value: os.stat_result) -> list[str]:
    ctime = "not-recorded" if os.name == "nt" else str(value.st_ctime_ns)
    return [
        str(value.st_dev),
        str(value.st_ino),
        str(stat.S_IFMT(value.st_mode)),
        str(stat.S_IMODE(value.st_mode)),
        str(value.st_nlink),
        str(value.st_size),
        str(value.st_mtime_ns),
        ctime,
    ]


def _object_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _root_identity(path: Path, value: os.stat_result) -> str:
    canonical_path = os.path.normcase(os.path.normpath(str(path)))
    return _digest_object(
        {
            "canonical_path": canonical_path,
            "device": str(value.st_dev),
            "file_identity": str(value.st_ino),
            "type": "directory",
        }
    )


def _namespace_identity(entries: dict[str, tuple[str, ...]]) -> str:
    return _digest_object(
        {
            "entries": [
                {"opaque_name": _digest_text(name), "stat": list(record)}
                for name, record in sorted(entries.items())
            ]
        }
    )


def _opaque_namespace_identity(
    namespace: str,
    name: str,
    record: tuple[str, ...],
) -> str:
    return _digest_object({"namespace": namespace, "name": name, "stat": list(record)})


def _quarantine_name(kind: str, generation_identity: str) -> str:
    prefix = "retrieval" if kind == RETRIEVAL_STAGING_KIND else "bridge"
    return f"mf106-{prefix}-{generation_identity.removeprefix('sha256:')}"


def _cleanup_audit_name(receipt_digest: str) -> str:
    return f"{_AUDIT_PREFIX}{receipt_digest.removeprefix('sha256:')}{_AUDIT_SUFFIX}"


def _receipt_identity(receipt_id: str) -> str:
    return _digest_text(receipt_id)


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_object(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "cleanup metadata cannot be canonicalized"
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalized_absolute_root(value: str | Path, label: str) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip():
        raise MillefeuilleContractError(f"{label} must be a non-empty path")
    path = Path(raw)
    if not path.is_absolute():
        raise MillefeuilleContractError(f"{label} must be absolute")
    raw_parts = re.split(r"[\\/]", raw)
    if any(part in {".", ".."} for part in raw_parts):
        raise MillefeuilleContractError(
            f"{label} must not contain dot or parent traversal components"
        )
    normalized = Path(os.path.normpath(raw))
    _require_plain_ancestors(normalized, label=label)
    return normalized


def _require_plain_ancestors(path: Path, *, label: str) -> None:
    anchor = Path(path.anchor)
    current = anchor
    try:
        anchor_stat = os.lstat(anchor)
    except OSError as exc:
        raise MillefeuilleContractError(f"{label} anchor is unavailable") from exc
    if _is_link_or_reparse(anchor_stat) or not stat.S_ISDIR(anchor_stat.st_mode):
        raise MillefeuilleContractError(
            f"{label} must not traverse links, reparse points, or non-directories"
        )
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        if part in {"", "."}:
            continue
        current /= part
        try:
            current_stat = os.lstat(current)
        except FileNotFoundError as exc:
            raise MillefeuilleContractError(f"{label} does not exist") from exc
        except OSError as exc:
            raise MillefeuilleContractError(f"{label} is unavailable") from exc
        if _is_link_or_reparse(current_stat) or not stat.S_ISDIR(current_stat.st_mode):
            raise MillefeuilleContractError(
                f"{label} must not traverse links, reparse points, or non-directories"
            )


def _require_plain_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        value = os.lstat(path)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} does not exist") from exc
    except OSError as exc:
        raise MillefeuilleContractError(f"{label} cannot be inspected") from exc
    if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise MillefeuilleContractError(
            f"{label} must be a plain directory, not a link or reparse point"
        )
    return value


def _is_link_or_reparse(value: os.stat_result) -> bool:
    if stat.S_ISLNK(value.st_mode):
        return True
    if os.name != "nt":
        return False
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_tag = getattr(value, "st_reparse_tag", 0)
    return bool(attributes & _WINDOWS_REPARSE_POINT) or bool(reparse_tag)


def _entry_exists_no_follow(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MillefeuilleContractError(
            "cleanup namespace could not be inspected"
        ) from exc
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    left_text = os.path.normcase(os.path.normpath(str(left)))
    right_text = os.path.normcase(os.path.normpath(str(right)))
    try:
        common = os.path.commonpath((left_text, right_text))
    except ValueError:
        return False
    return common in {left_text, right_text}


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MillefeuilleContractError(
            "cleanup candidate path must be a non-empty trimmed string"
        )
    if len(value) > 256 or "\\" in value:
        raise MillefeuilleContractError(
            "cleanup candidate path must be a short slash-normalized relative path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise MillefeuilleContractError(
            "cleanup candidate path must not contain traversal"
        )
    normalized = path.as_posix()
    if normalized != value:
        raise MillefeuilleContractError(
            "cleanup candidate path must be slash-normalized"
        )
    return normalized


def _safe_flat_inventory_path(value: str) -> str:
    normalized = _safe_relative_path(value)
    if len(PurePosixPath(normalized).parts) != 1:
        raise MillefeuilleContractError(
            "temporary bridge inventory v0.1 supports flat relative files only"
        )
    return normalized


def _safe_identity(value: str, field_name: str) -> None:
    if _SAFE_ID_RE.fullmatch(value) is None or value in {
        "all",
        "any",
        "everything",
    }:
        raise MillefeuilleContractError(f"{field_name} is not an exact safe identity")


def _required_string(
    value: object,
    field_name: str,
    *,
    maximum: int = 256,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise MillefeuilleContractError(
            f"{field_name} must be a non-empty bounded string"
        )
    return value


def _required_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > _JSON_SAFE_INTEGER_MAX
    ):
        raise MillefeuilleContractError(f"{field_name} must be a safe integer")
    return value


def _require_exact_fields(
    payload: dict[str, Any],
    expected: Collection[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual != set(expected):
        raise MillefeuilleContractError(f"{label} fields are not exact")


def _load_json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_fields,
            parse_constant=_reject_non_finite_json_number,
        )
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return value


def _object_without_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, value in pairs:
        if name in payload:
            raise MillefeuilleContractError(
                "cleanup JSON contains duplicate object fields"
            )
        payload[name] = value
    return payload


def _reject_non_finite_json_number(value: str) -> None:
    del value
    raise MillefeuilleContractError("cleanup JSON contains a non-finite number")


def _reject_secret_like(value: object, label: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_FIELD_NAMES:
                raise MillefeuilleContractError(
                    f"{label} contains a forbidden secret-bearing field"
                )
            _reject_secret_like(nested, label)
        return
    if isinstance(value, list):
        for nested in value:
            _reject_secret_like(nested, label)
        return
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _FORBIDDEN_VALUE_MARKERS
    ):
        raise MillefeuilleContractError(
            f"{label} contains forbidden credential or private-byte material"
        )


def _normalize_evaluation_time(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise MillefeuilleContractError(
            "cleanup evaluation time must be timezone-aware"
        )
    return result.astimezone(UTC).replace(microsecond=0)


def _parse_utc_timestamp(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
        raise MillefeuilleContractError("cleanup timestamp must be canonical UTC")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise MillefeuilleContractError("cleanup timestamp is invalid") from exc
    return parsed


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory_fd(fd: int) -> None:
    if os.name != "posix":
        return
    os.fsync(fd)
