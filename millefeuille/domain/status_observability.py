"""Strict read-only joins for Millefeuille run status and local observations."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from millefeuille.domain.artifacts import (
    WRITEBACK_INCOMPLETE_STATUSES,
    ArtifactIndex,
)
from millefeuille.domain.millefeuille import (
    AcceptanceSummaryRecord,
    ClassificationDecisionRecord,
    ClassificationPlanRecord,
    MillefeuilleContractError,
    RetrievalIndexRecord,
    StageManifest,
    StageStatus,
    ZoteroWritebackPlanRecord,
)
from millefeuille.domain.model_execution import validate_model_provenance_record
from millefeuille.domain.secure_io import RootArtifactReader, read_bytes_no_follow
from millefeuille.domain.source_packs import parse_source_pack_manifest
from millefeuille.domain.stage_runtime import resolve_run_artifacts, stage_status_map

STATUS_SCHEMA_VERSION = "millefeuille-status/v0.2"
STATUS_OBSERVATION_SCHEMA_VERSION = "millefeuille-status-observation/v0.1"
STATUS_OBSERVATION_SOURCE = "operator-supplied-local-observation"

OBSERVATION_KINDS = (
    "index-ledger",
    "provider-usage",
    "quality",
    "writeback-result",
    "zotero-live-state",
)

_CANONICAL_JSON_MAX_BYTES = 1_048_576
_OBSERVATION_MAX_BYTES = 65_536
_JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
_JSON_MAX_DEPTH = 64
_JSON_MAX_NODES = 20_000
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SOURCE_HASH_RE = re.compile(r"sha256(?:-aggregate)?:[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_SAFE_REF_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+\-]{0,127}\Z")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)

_INDEX_TOP_FIELDS = frozenset(
    {
        "schema_version",
        "paper_id",
        "run_id",
        "artifact_root",
        "source_pack",
        "source_identity",
        "stages",
        "artifacts",
        "indexes",
        "zotero_writeback",
    }
)
_SOURCE_PACK_INDEX_FIELDS = frozenset(
    {"ref", "source_type", "source_hash", "manifest_ref"}
)
_ARTIFACT_RECORD_FIELDS = frozenset(
    {"kind", "ref", "format", "stage", "private_content"}
)
_INDEX_LANE_FIELDS = frozenset({"lane", "status", "result_ref", "skip_reason"})
_WRITEBACK_INDEX_FIELDS = frozenset({"mode", "status", "plan_ref", "result_ref"})
_STAGE_TOP_FIELDS = frozenset(
    {"schema_version", "run_id", "source_type", "mode", "manual_gates", "stages"}
)
_STAGE_RECORD_FIELDS = frozenset(
    {"name", "status", "inputs", "outputs", "manual_gate_required", "gate", "notes"}
)
_INTEGRITY_FIELDS = frozenset({"algorithm", "content_digest"})
_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "authority",
        "paper_id",
        "run_id",
        "source_hash",
        "summary",
        "integrity",
    }
)
_AUTHORITY_FIELDS = frozenset({"source", "authoritative"})

_COMPLETION_GATE_SCHEMA_VERSION = "millefeuille-completion-gate-result/v0.1"
_WRITEBACK_RESULT_SCHEMA_VERSION = "millefeuille-zotero-writeback-result/v0.1"
_COMPLETION_GATE_FIELDS = frozenset(
    {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "status",
        "checks_total",
        "checks_passed",
        "checks_failed",
        "integrity",
    }
)
_WRITEBACK_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "mode",
        "status",
        "source_item_version",
        "item_version",
        "operation_count",
        "receipt_digest",
        "audit_digest",
        "integrity",
    }
)

_FORBIDDEN_OBSERVATION_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credentials",
        "filename",
        "headers",
        "item_metadata",
        "ledger_id",
        "note",
        "notes",
        "password",
        "path",
        "payload",
        "private_excerpt",
        "provider_request",
        "provider_response",
        "raw_tags",
        "refresh_token",
        "request_text",
        "response_text",
        "secret",
        "tags",
        "url",
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
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"https?://", re.IGNORECASE),
)


@dataclass(frozen=True)
class _StatusContext:
    artifact_index: ArtifactIndex
    artifact_index_payload: dict[str, Any]
    run_dir: Path
    boundary: Path
    stage_manifest: StageManifest | None
    stage_manifest_payload: dict[str, Any] | None
    source_pack_payload: dict[str, Any] | None
    source_pack_dir: Path | None
    scope: str
    reader: RootArtifactReader | None = None
    portable_snapshots: dict[Path, str] | None = None

    @property
    def paper_id(self) -> str:
        return self.artifact_index.paper_id

    @property
    def run_id(self) -> str:
        return self.artifact_index.run_id

    @property
    def source_hash(self) -> str:
        return str(self.artifact_index.source_pack["source_hash"])

    @property
    def mode(self) -> str:
        if self.stage_manifest is None:
            return "not-observed"
        return self.stage_manifest.mode.value


def compute_status_observation_digest(payload: dict[str, Any]) -> str:
    """Return the content identity of a local observation envelope."""

    body = dict(payload)
    body.pop("integrity", None)
    return _digest_object(body)


def compute_status_digest(payload: dict[str, Any]) -> str:
    """Return the deterministic content identity of one sanitized status."""

    body = dict(payload)
    body.pop("integrity", None)
    return _digest_object(body)


def load_status_observation(path: str | Path) -> dict[str, Any]:
    """Load one bounded, duplicate-free, content-addressed local observation."""

    target = _validated_operator_path(path, label="status observation")
    directory_snapshot = (
        _portable_directory_snapshot(target.parent, label="status observation parent")
        if not hasattr(os, "O_NOFOLLOW")
        else None
    )
    payload = _load_strict_json_bytes(
        read_bytes_no_follow(
            target,
            "status observation",
            max_bytes=_OBSERVATION_MAX_BYTES,
        ),
        "status observation",
    )
    _validate_observation(payload)
    if directory_snapshot is not None and directory_snapshot != (
        _portable_directory_snapshot(
            target.parent,
            label="status observation parent",
        )
    ):
        raise MillefeuilleContractError("status observation parent changed during read")
    return payload


def build_status_report(
    *,
    index_path: str | Path | None = None,
    source_pack_root: str | Path | None = None,
    paper_id: str | None = None,
    item_key: str | None = None,
    run_id: str | None = None,
    evidence_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Build a deterministic read-only progressive join for one run."""

    if source_pack_root is not None:
        if index_path is not None:
            raise MillefeuilleContractError(
                "status source-pack root and direct index modes are mutually exclusive"
            )
        if not run_id:
            raise MillefeuilleContractError(
                "status --source-pack-root requires an exact run_id"
            )
        if bool(paper_id) == bool(item_key):
            raise MillefeuilleContractError(
                "status --source-pack-root requires exactly one paper_id or item_key"
            )
        return _build_canonical_status(
            source_pack_root=source_pack_root,
            paper_id=paper_id,
            item_key=item_key,
            run_id=run_id,
            evidence_paths=evidence_paths,
        )
    if index_path is None:
        raise MillefeuilleContractError(
            "status requires a source-pack root or artifact index"
        )
    if item_key is not None:
        raise MillefeuilleContractError(
            "status item_key is supported only with --source-pack-root"
        )
    return _build_progressive_index_status(
        index_path=index_path,
        paper_id=paper_id,
        run_id=run_id,
        evidence_paths=evidence_paths,
    )


def _build_canonical_status(
    *,
    source_pack_root: str | Path,
    paper_id: str | None,
    item_key: str | None,
    run_id: str,
    evidence_paths: Sequence[str | Path],
) -> dict[str, Any]:
    root = _validated_operator_path(
        source_pack_root,
        label="source-pack root",
    )
    if not hasattr(os, "O_NOFOLLOW"):
        return _build_portable_canonical_status(
            root=root,
            paper_id=paper_id,
            item_key=item_key,
            run_id=run_id,
            evidence_paths=evidence_paths,
        )
    with RootArtifactReader(root) as reader:
        resolved = resolve_run_artifacts(
            source_pack_root=root,
            run_id=run_id,
            paper_id=paper_id,
            item_key=item_key,
            artifact_reader=reader,
        )
        source_payload = _load_reader_json(
            reader,
            resolved.source_pack_dir / "manifest.json",
            "source-pack manifest",
        )
        stage_payload = _load_reader_json(
            reader,
            resolved.stage_manifest_path,
            "stage manifest",
        )
        index_payload = _load_reader_json(
            reader,
            resolved.artifact_index_path,
            "artifact index",
        )
        _validate_stage_manifest_payload(stage_payload)
        _validate_artifact_index_payload(index_payload)
        parsed_source = parse_source_pack_manifest(source_payload)
        parsed_stage = StageManifest.from_dict(stage_payload)
        parsed_index = ArtifactIndex.from_dict(index_payload)
        _require_identity(
            parsed_index,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=str(parsed_source.get("source_hash", "")),
        )
        _require_stage_index_match(parsed_stage, parsed_index)
        _require_canonical_refs(
            parsed_index,
            stage_manifest=parsed_stage,
            run_dir=resolved.run_dir,
            source_pack_dir=resolved.source_pack_dir,
        )
        context = _StatusContext(
            artifact_index=parsed_index,
            artifact_index_payload=index_payload,
            run_dir=resolved.run_dir,
            boundary=resolved.source_pack_dir,
            stage_manifest=parsed_stage,
            stage_manifest_payload=stage_payload,
            source_pack_payload=parsed_source,
            source_pack_dir=resolved.source_pack_dir,
            scope="canonical-source-pack",
            reader=reader,
        )
        report = _join_status(context, evidence_paths=evidence_paths)
        reader.revalidate_snapshot()
        return report


def _build_portable_canonical_status(
    *,
    root: Path,
    paper_id: str | None,
    item_key: str | None,
    run_id: str,
    evidence_paths: Sequence[str | Path],
) -> dict[str, Any]:
    root_snapshot = _portable_directory_snapshot(root, label="source-pack root")
    resolved = resolve_run_artifacts(
        source_pack_root=root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
    )
    source_path = resolved.source_pack_dir / "manifest.json"
    source_payload = _load_path_json(source_path, "source-pack manifest")
    stage_payload = _load_path_json(resolved.stage_manifest_path, "stage manifest")
    index_payload = _load_path_json(resolved.artifact_index_path, "artifact index")
    _validate_stage_manifest_payload(stage_payload)
    _validate_artifact_index_payload(index_payload)
    parsed_source = parse_source_pack_manifest(source_payload)
    parsed_stage = StageManifest.from_dict(stage_payload)
    parsed_index = ArtifactIndex.from_dict(index_payload)
    _require_identity(
        parsed_index,
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=str(parsed_source.get("source_hash", "")),
    )
    _require_stage_index_match(parsed_stage, parsed_index)
    _require_canonical_refs(
        parsed_index,
        stage_manifest=parsed_stage,
        run_dir=resolved.run_dir,
        source_pack_dir=resolved.source_pack_dir,
    )
    context = _StatusContext(
        artifact_index=parsed_index,
        artifact_index_payload=index_payload,
        run_dir=resolved.run_dir,
        boundary=resolved.source_pack_dir,
        stage_manifest=parsed_stage,
        stage_manifest_payload=stage_payload,
        source_pack_payload=parsed_source,
        source_pack_dir=resolved.source_pack_dir,
        scope="canonical-source-pack",
        portable_snapshots={},
    )
    report = _join_status(context, evidence_paths=evidence_paths)
    # Portable platforms cannot pin a multi-file root, so bind the returned
    # snapshot to a second complete set of stable per-file reads.
    if (
        _digest_object(_load_path_json(source_path, "source-pack manifest"))
        != _digest_object(source_payload)
        or _digest_object(
            _load_path_json(resolved.stage_manifest_path, "stage manifest")
        )
        != _digest_object(stage_payload)
        or _digest_object(
            _load_path_json(resolved.artifact_index_path, "artifact index")
        )
        != _digest_object(index_payload)
    ):
        raise MillefeuilleContractError(
            "canonical status inputs changed during observation"
        )
    _revalidate_portable_artifacts(context)
    if root_snapshot != _portable_directory_snapshot(
        root,
        label="source-pack root",
    ):
        raise MillefeuilleContractError(
            "source-pack root changed during canonical observation"
        )
    return report


def _build_progressive_index_status(
    *,
    index_path: str | Path,
    paper_id: str | None,
    run_id: str | None,
    evidence_paths: Sequence[str | Path],
) -> dict[str, Any]:
    target = _validated_operator_path(index_path, label="artifact index")
    index_payload = _load_path_json(target, "artifact index")
    _validate_artifact_index_payload(index_payload)
    artifact_index = ArtifactIndex.from_dict(index_payload)
    if paper_id is not None and artifact_index.paper_id != paper_id:
        raise MillefeuilleContractError("artifact index paper_id drift")
    if run_id is not None and artifact_index.run_id != run_id:
        raise MillefeuilleContractError("artifact index run_id drift")
    if (
        _SOURCE_HASH_RE.fullmatch(
            str(artifact_index.source_pack.get("source_hash", ""))
        )
        is None
    ):
        raise MillefeuilleContractError("artifact index source_hash is invalid")

    run_dir = target.absolute().parent
    stage_path = run_dir / "stage-manifest.json"
    stage_payload = _probe_path_json(stage_path, "stage manifest")
    stage_manifest: StageManifest | None = None
    if stage_payload is not None:
        _validate_stage_manifest_payload(stage_payload)
        stage_manifest = StageManifest.from_dict(stage_payload)
        _require_stage_index_match(stage_manifest, artifact_index)
    context = _StatusContext(
        artifact_index=artifact_index,
        artifact_index_payload=index_payload,
        run_dir=run_dir,
        boundary=run_dir,
        stage_manifest=stage_manifest,
        stage_manifest_payload=stage_payload,
        source_pack_payload=None,
        source_pack_dir=None,
        scope="artifact-index-progressive",
    )
    return _join_status(context, evidence_paths=evidence_paths)


def _join_status(
    context: _StatusContext,
    *,
    evidence_paths: Sequence[str | Path],
) -> dict[str, Any]:
    artifact_status = context.artifact_index.status()
    blockers = list(artifact_status.blocking_items)
    if context.source_pack_payload is None:
        blockers.append("source-pack identity: not-observed")
    if context.stage_manifest is None:
        blockers.append("stage manifest: not-observed")

    index_join = _join_index(context, blockers)
    acceptance_join = _join_acceptance(context, blockers)
    classification_join = _join_classification(context, blockers)
    writeback_join = _join_writeback(context, blockers)
    observation_claims = _canonical_observation_claims(
        context,
        index_join=index_join,
        writeback_join=writeback_join,
    )
    required_observations = _required_observations(
        context,
        claims=observation_claims,
        writeback_join=writeback_join,
    )
    observations = _join_observations(
        evidence_paths,
        context=context,
        required=required_observations,
        claims=observation_claims,
        blockers=blockers,
    )
    _require_claimed_live_writeback(
        context,
        writeback_join=writeback_join,
        observations=observations,
        blockers=blockers,
    )

    unique_blockers = sorted(set(blockers))
    complete = not unique_blockers
    observationally_ready = context.mode == "approved-live" and complete
    if observationally_ready:
        state = "observational-live-ready"
    elif complete and context.mode == "preview":
        state = "preview-complete"
    elif complete:
        state = "observed-complete"
    else:
        state = "needs-review"

    stage_items = (
        [
            {"name": stage.name.value, "status": stage.status.value}
            for stage in context.stage_manifest.stages
        ]
        if context.stage_manifest is not None
        else []
    )
    payload: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "paper_id": context.paper_id,
        "run_id": context.run_id,
        "source_hash": context.source_hash,
        "mode": context.mode,
        "observation_scope": context.scope,
        "authority": {
            "source": STATUS_OBSERVATION_SOURCE,
            "authoritative": False,
        },
        "status": {
            "state": state,
            "complete": complete,
            "observationally_ready": observationally_ready,
            "authoritative_live_complete": False,
            "blocking_items": unique_blockers,
            "stage_counts": dict(sorted(artifact_status.stage_counts.items())),
            "index_counts": dict(sorted(artifact_status.index_counts.items())),
        },
        "canonical": {
            "source_pack": _component_record(context.source_pack_payload),
            "stage_manifest": _component_record(context.stage_manifest_payload),
            "artifact_index": _component_record(context.artifact_index_payload),
        },
        "stages": stage_items,
        "index": index_join,
        "acceptance": acceptance_join,
        "classification": classification_join,
        "zotero_writeback": writeback_join,
        "observations": observations,
        "private_content_emitted": False,
        "credentials_loaded": False,
        "external_calls_performed": False,
    }
    payload["integrity"] = {
        "algorithm": "sha256",
        "content_digest": compute_status_digest(payload),
    }
    return payload


def _component_record(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "state": "observed" if payload is not None else "not-observed",
        "content_digest": _digest_object(payload) if payload is not None else None,
    }


def _join_index(
    context: _StatusContext,
    blockers: list[str],
) -> dict[str, Any]:
    lanes = [
        {"lane": record["lane"], "status": record["status"]}
        for record in sorted(
            context.artifact_index.indexes,
            key=lambda value: str(value["lane"]),
        )
    ]
    index_stage = _stage_status(context, "index")
    for indexed_lane in context.artifact_index.indexes:
        result_ref = indexed_lane.get("result_ref")
        if result_ref is not None:
            result_text = _required_string(result_ref, "index lane result_ref")
            result_path = _resolve_artifact_ref(
                result_text,
                run_dir=context.run_dir,
                boundary=context.boundary,
                label="index lane result_ref",
            )
            if context.stage_manifest is not None:
                _verify_regular_artifact(
                    context,
                    result_path,
                    label="index lane result_ref",
                )
                _require_stage_output(
                    context,
                    stage="index",
                    ref=result_text,
                    label="index lane result_ref",
                )
        if indexed_lane["status"] == "written" and result_ref is None:
            raise MillefeuilleContractError(
                "written artifact-index lane must bind result_ref"
            )
    record = _joined_artifact_record(
        context,
        name="retrieval_index_status",
        kind="retrieval-index-status",
        stage="index",
    )
    if record is None:
        if _stage_requires_evidence(context, "index"):
            blockers.append("index evidence: not-observed")
        return {
            "state": "not-observed",
            "lanes": lanes,
            "content_digest": None,
        }

    payload = _read_artifact_json(
        context,
        record,
        label="retrieval index status",
    )
    _validate_retrieval_index_payload(payload)
    index_status = RetrievalIndexRecord.from_dict(payload)
    _require_record_identity(
        paper_id=index_status.paper_id,
        run_id=index_status.run_id,
        source_hash=index_status.source_hash,
        context=context,
        label="retrieval index status",
    )
    observed_lanes = sorted(
        ({"lane": lane.lane, "status": lane.status} for lane in index_status.lanes),
        key=lambda value: value["lane"],
    )
    if observed_lanes != lanes:
        raise MillefeuilleContractError(
            "retrieval index status lanes drift from artifact index"
        )
    status_path = _artifact_target(context, record, label="retrieval index status")
    expected_refs = {
        "selected_fulltext_ref": _expected_relative_ref(
            _required_source_pack_artifact_target(
                context,
                name="selected_fulltext",
                kind="selected-fulltext",
                format_name="markdown",
                stage="route",
                private_content=True,
                source_relative=Path("selected/fulltext.md"),
            ),
            start=status_path.parent,
            label="retrieval selected_fulltext_ref",
        ),
        "summary_ref": _expected_relative_ref(
            _required_indexed_artifact_target(
                context,
                name="hierarchical_summary",
                kind="hierarchical-summary",
                format_name="json",
                stage="summarize",
                private_content=False,
            ),
            start=status_path.parent,
            label="retrieval summary_ref",
        ),
        "paper_card_ref": _expected_relative_ref(
            _required_indexed_artifact_target(
                context,
                name="paper_card_json",
                kind="paper-card",
                format_name="json",
                stage="card",
                private_content=False,
            ),
            start=status_path.parent,
            label="retrieval paper_card_ref",
        ),
    }
    observed_refs = {
        "selected_fulltext_ref": index_status.selected_fulltext_ref,
        "summary_ref": index_status.summary_ref,
        "paper_card_ref": index_status.paper_card_ref,
    }
    if observed_refs != expected_refs:
        raise MillefeuilleContractError(
            "retrieval index artifact refs drift from the artifact index"
        )
    if index_stage == StageStatus.PASSED.value and not lanes:
        raise MillefeuilleContractError("passed index stage must bind exact lanes")
    return {
        "state": "observed",
        "lanes": lanes,
        "content_digest": _digest_object(payload),
    }


def _join_acceptance(
    context: _StatusContext,
    blockers: list[str],
) -> dict[str, Any]:
    record = _joined_artifact_record(
        context,
        name="acceptance_summary",
        kind="acceptance-summary",
        stage="acceptance",
    )
    if record is None:
        if _stage_requires_evidence(context, "acceptance"):
            blockers.append("acceptance evidence: not-observed")
        return {
            "state": "not-observed",
            "status": None,
            "content_digest": None,
        }
    payload = _read_artifact_json(context, record, label="acceptance summary")
    _validate_acceptance_payload(payload)
    summary = AcceptanceSummaryRecord.from_dict(payload)
    _require_record_identity(
        paper_id=summary.paper_id,
        run_id=summary.run_id,
        source_hash=summary.source_hash,
        context=context,
        label="acceptance summary",
    )
    if context.source_pack_dir is not None:
        expected_source_ref = _expected_relative_ref(
            context.source_pack_dir,
            start=context.run_dir,
            label="acceptance source_pack_ref",
        )
        if summary.source_pack_ref != expected_source_ref:
            raise MillefeuilleContractError("acceptance summary source_pack_ref drift")
    stage_status = _stage_status(context, "acceptance")
    expected_stage = "passed" if summary.status.value == "pass" else "needs-review"
    if stage_status not in {None, expected_stage}:
        raise MillefeuilleContractError(
            "acceptance summary status drifts from the canonical stage"
        )
    if summary.status.value != "pass":
        blockers.append(f"acceptance: {summary.status.value}")
    return {
        "state": "observed",
        "status": summary.status.value,
        "content_digest": _digest_object(payload),
    }


def _join_classification(
    context: _StatusContext,
    blockers: list[str],
) -> dict[str, Any]:
    plan_record = _joined_artifact_record(
        context,
        name="classification_plan",
        kind="classification-plan",
        stage="classify",
    )
    decision_record = _joined_artifact_record(
        context,
        name="classification_decision",
        kind="classification-decision",
        stage="classify",
    )
    if plan_record is None and decision_record is None:
        if _stage_requires_evidence(context, "classify"):
            blockers.append("classification evidence: not-observed")
        return {
            "state": "not-observed",
            "status": None,
            "content_digest": None,
        }
    if plan_record is None or decision_record is None:
        blockers.append("classification evidence: incomplete")
        return {
            "state": "incomplete",
            "status": None,
            "content_digest": None,
        }

    plan_payload = _read_artifact_json(
        context,
        plan_record,
        label="classification plan",
    )
    decision_payload = _read_artifact_json(
        context,
        decision_record,
        label="classification decision",
    )
    _validate_classification_plan_payload(plan_payload)
    _validate_classification_decision_payload(decision_payload)
    plan = ClassificationPlanRecord.from_dict(plan_payload)
    decision = ClassificationDecisionRecord.from_dict(decision_payload)
    if plan.run_id != context.run_id:
        raise MillefeuilleContractError("classification plan run_id drift")
    if len(plan.papers) != 1 or plan.papers[0].get("paper_id") != context.paper_id:
        raise MillefeuilleContractError(
            "classification status supports exactly one canonical paper"
        )
    _require_record_identity(
        paper_id=decision.paper_id,
        run_id=decision.run_id,
        source_hash=decision.source_hash,
        context=context,
        label="classification decision",
    )
    plan_paper = plan.papers[0]
    if plan_paper.get("decision_ref") != decision_record["ref"]:
        raise MillefeuilleContractError(
            "classification plan decision_ref drifts from the artifact index"
        )
    if (
        plan.mode.value != decision.mode.value
        or plan.taxonomy_version != decision.taxonomy_version
        or plan_paper.get("status") != decision.status.value
    ):
        raise MillefeuilleContractError(
            "classification plan mode, taxonomy, or status drifts from decision"
        )
    stage_status = _stage_status(context, "classify")
    expected_stage = (
        "passed" if decision.status.value == "classified" else "needs-review"
    )
    if stage_status not in {None, expected_stage}:
        raise MillefeuilleContractError(
            "classification decision status drifts from the canonical stage"
        )
    if decision.status.value != "classified":
        blockers.append(f"classification: {decision.status.value}")
    return {
        "state": "observed",
        "status": decision.status.value,
        "content_digest": _digest_object(
            {
                "plan": _digest_object(plan_payload),
                "decision": _digest_object(decision_payload),
            }
        ),
    }


def _join_writeback(
    context: _StatusContext,
    blockers: list[str],
) -> dict[str, Any]:
    indexed = context.artifact_index.zotero_writeback
    mode = str(indexed["mode"])
    status = str(indexed["status"])
    plan_ref = indexed.get("plan_ref")
    result_ref = indexed.get("result_ref")
    if status not in WRITEBACK_INCOMPLETE_STATUSES:
        if mode == "none" and status not in {"not-planned", "skipped"}:
            raise MillefeuilleContractError("writeback none mode has invalid status")
        if mode == "preview" and status != "previewed":
            raise MillefeuilleContractError("writeback preview mode must be previewed")
        if mode == "approved-live" and status not in {"written", "skipped"}:
            raise MillefeuilleContractError(
                "writeback approved-live mode has invalid status"
            )
    if (
        mode in {"preview", "approved-live"}
        and status not in WRITEBACK_INCOMPLETE_STATUSES
        and not isinstance(plan_ref, str)
    ):
        raise MillefeuilleContractError("writeback mode must bind exact plan_ref")
    if status == "written" and not isinstance(result_ref, str):
        raise MillefeuilleContractError("written writeback must bind exact result_ref")
    if mode != "approved-live" and result_ref is not None:
        raise MillefeuilleContractError(
            "only approved-live writeback may bind result_ref"
        )
    if status in WRITEBACK_INCOMPLETE_STATUSES:
        blockers.append(f"zotero_writeback {mode}: {status}")

    record = _joined_artifact_record(
        context,
        name="zotero_writeback_plan",
        kind="zotero-writeback-plan",
        stage="writeback",
    )
    content_digest: str | None = None
    result_digest: str | None = None
    state = "not-observed"
    if record is not None:
        if plan_ref != record["ref"]:
            raise MillefeuilleContractError(
                "Zotero writeback plan_ref drifts from artifact index artifact"
            )
        payload = _read_artifact_json(
            context,
            record,
            label="Zotero writeback plan",
        )
        _validate_writeback_plan_payload(payload)
        plan = ZoteroWritebackPlanRecord.from_dict(payload)
        _require_record_identity(
            paper_id=plan.paper_id,
            run_id=plan.run_id,
            source_hash=plan.source_hash,
            context=context,
            label="Zotero writeback plan",
        )
        if plan.mode != mode or plan.status != status:
            raise MillefeuilleContractError(
                "Zotero writeback plan drifts from artifact index"
            )
        state = "observed"
        content_digest = _digest_object(payload)
    elif mode == "preview" and status == "previewed":
        preview_record = _joined_artifact_record(
            context,
            name="zotero_writeback_preview",
            kind="zotero-writeback-preview",
            stage="classify",
        )
        if preview_record is not None:
            if plan_ref != preview_record["ref"]:
                raise MillefeuilleContractError(
                    "Zotero writeback preview plan_ref drifts from artifact index"
                )
            # The preview may contain proposed tags and note text. Its exact
            # index/stage ownership is joined without opening private details.
            state = "indexed-preview"
        elif context.stage_manifest is not None and plan_ref is not None:
            raise MillefeuilleContractError(
                "artifact-index writeback plan_ref is not bound to a canonical artifact"
            )
    elif context.stage_manifest is not None and plan_ref is not None:
        raise MillefeuilleContractError(
            "artifact-index writeback plan_ref is not bound to a canonical artifact"
        )
    elif _stage_requires_evidence(context, "writeback"):
        blockers.append("writeback plan: not-observed")

    result_record = _joined_artifact_record(
        context,
        name="zotero_writeback_result",
        kind="zotero-writeback-result",
        stage="writeback",
    )
    if result_record is not None:
        if result_ref != result_record["ref"]:
            raise MillefeuilleContractError(
                "Zotero writeback result_ref drifts from artifact index artifact"
            )
        result_payload = _read_artifact_json(
            context,
            result_record,
            label="Zotero writeback result",
        )
        _validate_writeback_result_payload(result_payload, context=context)
        if result_payload["mode"] != mode or result_payload["status"] != status:
            raise MillefeuilleContractError(
                "Zotero writeback result drifts from artifact index"
            )
        result_digest = str(result_payload["integrity"]["content_digest"])
    elif context.stage_manifest is not None and result_ref is not None:
        raise MillefeuilleContractError(
            "artifact-index writeback result_ref is not bound to a canonical artifact"
        )

    stage_status = _stage_status(context, "writeback")
    if context.mode == "approved-live" and stage_status == "passed":
        completion = "live-result-required"
        if mode != "approved-live" or status != "written":
            blockers.append("approved-live writeback: exact written result not claimed")
    elif (
        context.mode == "preview"
        and mode == "preview"
        and status == "previewed"
        and state in {"observed", "indexed-preview"}
    ):
        completion = "preview-complete"
    elif mode == "none" and status in {"not-planned", "skipped"}:
        completion = "not-applicable"
    else:
        completion = "incomplete"
        if stage_status == "passed":
            blockers.append(f"writeback {mode}: {status}")
    return {
        "state": state,
        "mode": mode,
        "status": status,
        "completion": completion,
        "content_digest": content_digest,
        "result_content_digest": result_digest,
    }


def _canonical_observation_claims(
    context: _StatusContext,
    *,
    index_join: dict[str, Any],
    writeback_join: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    live_writeback_claimed = (
        context.mode == "approved-live"
        and _stage_status(context, "writeback") == StageStatus.PASSED.value
    )
    source_item_version = _canonical_source_zotero_version(
        context.source_pack_payload,
        required=live_writeback_claimed,
    )
    if source_item_version is not None:
        claims["zotero-live-state"] = {"summary": {"item_version": source_item_version}}
    written_lanes = [
        {"lane": lane["lane"], "status": "written"}
        for lane in index_join["lanes"]
        if lane["status"] == "written"
    ]
    if written_lanes and isinstance(index_join["content_digest"], str):
        claims["index-ledger"] = {
            "summary": {
                "lanes": written_lanes,
                "index_status_digest": index_join["content_digest"],
            }
        }

    if context.stage_manifest is not None:
        provenance_names = [
            name
            for name in ("model_provenance", "model_provenance_record")
            if name in context.artifact_index.artifacts
        ]
        if len(provenance_names) > 1:
            raise MillefeuilleContractError(
                "artifact index contains ambiguous model provenance records"
            )
        if provenance_names:
            record = _require_indexed_artifact_record(
                context,
                name=provenance_names[0],
                kind="model-provenance-record",
                format_name="json",
                stage="summarize",
                private_content=False,
            )
            payload = _read_artifact_json(
                context,
                record,
                label="model provenance record",
            )
            validated = validate_model_provenance_record(payload)
            usage = validated["usage"]
            claims["provider-usage"] = {
                "stage": "summarize",
                "summary": {
                    "record_digest": _digest_object(payload),
                    "call_count": 1,
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                },
            }

        if "completion_gate_result" in context.artifact_index.artifacts:
            record = _require_indexed_artifact_record(
                context,
                name="completion_gate_result",
                kind="completion-gate-result",
                format_name="json",
                stage="release",
                private_content=False,
            )
            payload = _read_artifact_json(
                context,
                record,
                label="completion gate result",
            )
            _validate_completion_gate_payload(payload, context=context)
            expected_stage = {
                "passed": StageStatus.PASSED.value,
                "needs-review": StageStatus.NEEDS_REVIEW.value,
                "failed": StageStatus.FAILED.value,
            }[str(payload["status"])]
            if _stage_status(context, "release") != expected_stage:
                raise MillefeuilleContractError(
                    "completion gate status drifts from the release stage"
                )
            claims["quality"] = {
                "stage": "release",
                "summary": {
                    "status": payload["status"],
                    "checks_total": payload["checks_total"],
                    "checks_passed": payload["checks_passed"],
                    "checks_failed": payload["checks_failed"],
                    "record_digest": payload["integrity"]["content_digest"],
                },
            }

        if writeback_join["result_content_digest"] is not None:
            record = _require_indexed_artifact_record(
                context,
                name="zotero_writeback_result",
                kind="zotero-writeback-result",
                format_name="json",
                stage="writeback",
                private_content=False,
            )
            payload = _read_artifact_json(
                context,
                record,
                label="Zotero writeback result",
            )
            _validate_writeback_result_payload(payload, context=context)
            claims["writeback-result"] = {
                "stage": "writeback",
                "summary": {
                    "status": payload["status"],
                    "operation_count": payload["operation_count"],
                    "receipt_digest": payload["receipt_digest"],
                    "audit_digest": payload["audit_digest"],
                    "record_digest": payload["integrity"]["content_digest"],
                },
            }
            claims["zotero-live-state"] = {
                "summary": {"item_version": payload["item_version"]}
            }
    return claims


def _canonical_source_zotero_version(
    source_pack: dict[str, Any] | None,
    *,
    required: bool,
) -> int | None:
    if source_pack is None:
        if required:
            raise MillefeuilleContractError(
                "approved-live status requires one exact source Zotero version"
            )
        return None
    schema_version = source_pack.get("schema_version")
    versions: list[object] = []
    if schema_version == "millefeuille-source-pack-manifest/v0.1":
        identity = source_pack.get("identity")
        if isinstance(identity, dict):
            versions.append(identity.get("zotero_version"))
    elif schema_version == "millefeuille-source-pack-manifest/v0.2":
        sources = source_pack.get("sources")
        if isinstance(sources, list):
            for source in sources:
                identity = source.get("identity") if isinstance(source, dict) else None
                versions.append(
                    identity.get("zotero_version")
                    if isinstance(identity, dict)
                    else None
                )
    exact_versions = {
        value
        for value in versions
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _JSON_SAFE_INTEGER_MAX
    }
    complete = (
        bool(versions)
        and len(exact_versions) == 1
        and all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _JSON_SAFE_INTEGER_MAX
            for value in versions
        )
    )
    if complete:
        return next(iter(exact_versions))
    if required:
        raise MillefeuilleContractError(
            "approved-live status requires one exact source Zotero version"
        )
    return None


def _required_observations(
    context: _StatusContext,
    *,
    claims: dict[str, dict[str, Any]],
    writeback_join: dict[str, Any],
) -> frozenset[str]:
    required: set[str] = set()
    if (
        "index-ledger" in claims
        and _stage_status(context, "index") == StageStatus.PASSED.value
    ):
        required.add("index-ledger")
    provider_claim = claims.get("provider-usage")
    if (
        provider_claim is not None
        and _stage_status(context, str(provider_claim["stage"]))
        == StageStatus.PASSED.value
    ):
        required.add("provider-usage")
    quality_claim = claims.get("quality")
    if (
        quality_claim is not None
        and _stage_status(context, str(quality_claim["stage"]))
        == StageStatus.PASSED.value
    ):
        required.add("quality")
    if (
        context.mode == "approved-live"
        and _stage_status(context, "writeback") == "passed"
        and writeback_join["mode"] == "approved-live"
    ):
        required.update({"writeback-result", "zotero-live-state"})
    return frozenset(required)


def _join_observations(
    evidence_paths: Sequence[str | Path],
    *,
    context: _StatusContext,
    required: Collection[str],
    claims: dict[str, dict[str, Any]],
    blockers: list[str],
) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for path in evidence_paths:
        payload = load_status_observation(path)
        kind = str(payload["kind"])
        if kind in loaded:
            raise MillefeuilleContractError(
                "status observations contain a duplicate kind"
            )
        _require_record_identity(
            paper_id=str(payload["paper_id"]),
            run_id=str(payload["run_id"]),
            source_hash=str(payload["source_hash"]),
            context=context,
            label="status observation",
        )
        _require_observation_matches_claim(
            kind=kind,
            summary=_required_object(payload["summary"], "observation summary"),
            claims=claims,
        )
        loaded[kind] = payload

    joined: dict[str, dict[str, Any]] = {}
    for kind in OBSERVATION_KINDS:
        is_required = kind in required
        payload = loaded.get(kind)
        if payload is None:
            joined[kind] = {
                "state": "not-observed",
                "required": is_required,
                "source": None,
                "authoritative": False,
                "content_digest": None,
                "summary": None,
            }
            if is_required:
                blockers.append(f"{kind} observation: not-observed")
            continue
        summary = _required_object(payload["summary"], "observation summary")
        status = str(summary["status"])
        if _observation_status_blocks(kind, status):
            blockers.append(f"{kind} observation: {status}")
        if kind == "zotero-live-state":
            for state_name in ("tag_state", "note_state"):
                state = str(summary[state_name])
                if state != "consistent":
                    blockers.append(f"{kind} observation {state_name}: {state}")
        joined[kind] = {
            "state": "observed",
            "required": is_required,
            "source": STATUS_OBSERVATION_SOURCE,
            "authoritative": False,
            "content_digest": payload["integrity"]["content_digest"],
            "summary": dict(payload["summary"]),
        }
    return joined


def _require_observation_matches_claim(
    *,
    kind: str,
    summary: dict[str, Any],
    claims: dict[str, dict[str, Any]],
) -> None:
    claim = claims.get(kind)
    if claim is None:
        raise MillefeuilleContractError(
            f"{kind} observation has no canonical local claim to bind"
        )
    expected = _required_object(claim.get("summary"), "canonical observation claim")
    if kind == "zotero-live-state":
        if summary.get("item_version") != expected.get("item_version"):
            raise MillefeuilleContractError(
                "zotero-live-state observation drifts from canonical local evidence"
            )
        return
    if kind in {"provider-usage", "index-ledger"}:
        observed = {key: value for key, value in summary.items() if key != "status"}
        if observed != expected:
            raise MillefeuilleContractError(
                f"{kind} observation drifts from canonical local evidence"
            )
        return
    if summary != expected:
        raise MillefeuilleContractError(
            f"{kind} observation drifts from canonical local evidence"
        )


def _observation_status_blocks(kind: str, status: str) -> bool:
    allowed = {
        "zotero-live-state": {"consistent"},
        "provider-usage": {"within-approved-limits"},
        "index-ledger": {"consistent"},
        "quality": {"passed"},
        "writeback-result": {"written", "skipped"},
    }
    return status not in allowed[kind]


def _require_claimed_live_writeback(
    context: _StatusContext,
    *,
    writeback_join: dict[str, Any],
    observations: dict[str, dict[str, Any]],
    blockers: list[str],
) -> None:
    if not (
        context.mode == "approved-live"
        and _stage_status(context, "writeback") == "passed"
    ):
        return
    result = observations["writeback-result"]
    zotero = observations["zotero-live-state"]
    if (
        writeback_join["mode"] != "approved-live"
        or writeback_join["status"] != "written"
        or result["state"] != "observed"
        or result["summary"]["status"] != "written"
        or zotero["state"] != "observed"
        or zotero["summary"]["status"] != "consistent"
    ):
        blockers.append("approved-live writeback: exact observed result required")


def _stage_status(context: _StatusContext, name: str) -> str | None:
    if context.stage_manifest is not None:
        return stage_status_map(context.stage_manifest).get(name)
    record = context.artifact_index.stages.get(name)
    return str(record["status"]) if record is not None else None


def _stage_requires_evidence(context: _StatusContext, name: str) -> bool:
    return _stage_status(context, name) in {
        StageStatus.PASSED.value,
        StageStatus.FAILED.value,
        StageStatus.NEEDS_REVIEW.value,
    }


def _joined_artifact_record(
    context: _StatusContext,
    *,
    name: str,
    kind: str,
    stage: str,
) -> dict[str, Any] | None:
    if context.stage_manifest is None:
        return None
    value = context.artifact_index.artifacts.get(name)
    if value is None:
        return None
    return _require_indexed_artifact_record(
        context,
        name=name,
        kind=kind,
        format_name="json",
        stage=stage,
        private_content=False,
    )


def _require_indexed_artifact_record(
    context: _StatusContext,
    *,
    name: str,
    kind: str,
    format_name: str,
    stage: str,
    private_content: bool,
) -> dict[str, Any]:
    value = context.artifact_index.artifacts.get(name)
    if value is None:
        raise MillefeuilleContractError(
            f"artifact index must bind canonical artifact {name}"
        )
    if (
        value.get("kind") != kind
        or value.get("format") != format_name
        or value.get("stage") != stage
        or value.get("private_content") is not private_content
    ):
        raise MillefeuilleContractError(
            f"artifact index {name} record does not match its canonical contract"
        )
    ref = _required_string(value.get("ref"), f"artifact index {name} ref")
    target = _resolve_artifact_ref(
        ref,
        run_dir=context.run_dir,
        boundary=context.boundary,
        label=f"artifact index {name} ref",
    )
    _verify_regular_artifact(
        context,
        target,
        label=f"artifact index {name}",
    )
    _require_stage_output(
        context,
        stage=stage,
        ref=ref,
        label=f"artifact index {name} ref",
    )
    return value


def _required_indexed_artifact_target(
    context: _StatusContext,
    *,
    name: str,
    kind: str,
    format_name: str,
    stage: str,
    private_content: bool,
) -> Path:
    record = _require_indexed_artifact_record(
        context,
        name=name,
        kind=kind,
        format_name=format_name,
        stage=stage,
        private_content=private_content,
    )
    target = _artifact_target(context, record, label=f"artifact index {name} ref")
    _verify_regular_artifact(
        context,
        target,
        label=f"artifact index {name}",
    )
    return target


def _required_source_pack_artifact_target(
    context: _StatusContext,
    *,
    name: str,
    kind: str,
    format_name: str,
    stage: str,
    private_content: bool,
    source_relative: Path,
) -> Path:
    if context.source_pack_dir is None:
        raise MillefeuilleContractError(
            f"artifact index {name} cannot be joined without source-pack identity"
        )
    record = context.artifact_index.artifacts.get(name)
    if record is None or (
        record.get("kind") != kind
        or record.get("format") != format_name
        or record.get("stage") != stage
        or record.get("private_content") is not private_content
    ):
        raise MillefeuilleContractError(
            f"artifact index {name} record does not match its canonical contract"
        )
    target = context.source_pack_dir / source_relative
    expected_ref = _expected_relative_ref(
        target,
        start=context.run_dir,
        label=f"artifact index {name} ref",
    )
    if record.get("ref") != expected_ref:
        raise MillefeuilleContractError(f"artifact index {name} ref drift")
    _require_stage_output(
        context,
        stage=stage,
        ref=expected_ref,
        label=f"artifact index {name} ref",
    )
    _verify_regular_artifact(
        context,
        target,
        label=f"artifact index {name}",
    )
    return target


def _require_stage_output(
    context: _StatusContext,
    *,
    stage: str,
    ref: str,
    label: str,
) -> None:
    if context.stage_manifest is None:
        raise MillefeuilleContractError(
            f"{label} cannot be joined without a stage manifest"
        )
    matches = [
        record for record in context.stage_manifest.stages if record.name.value == stage
    ]
    if len(matches) != 1:
        raise MillefeuilleContractError(
            f"{label} requires exactly one canonical {stage} stage"
        )
    if ref not in matches[0].outputs:
        raise MillefeuilleContractError(
            f"{label} is not claimed by the canonical stage outputs"
        )


def _artifact_target(
    context: _StatusContext,
    record: dict[str, Any],
    *,
    label: str,
) -> Path:
    return _resolve_artifact_ref(
        str(record["ref"]),
        run_dir=context.run_dir,
        boundary=context.boundary,
        label=label,
    )


def _verify_regular_artifact(
    context: _StatusContext,
    target: Path,
    *,
    label: str,
) -> None:
    try:
        if context.reader is not None:
            context.reader.verify_regular_file(target, label)
            return
        value = os.lstat(target)
    except (OSError, MillefeuilleContractError) as exc:
        raise MillefeuilleContractError(
            f"{label} is not a stable canonical regular file"
        ) from exc
    attributes = int(getattr(value, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or attributes & _WINDOWS_REPARSE_POINT
    ):
        raise MillefeuilleContractError(
            f"{label} is not a stable canonical regular file"
        )


def _expected_relative_ref(target: Path, *, start: Path, label: str) -> str:
    try:
        ref = os.path.relpath(target, start=start).replace("\\", "/")
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"{label} is not on the canonical local filesystem"
        ) from exc
    if not ref or ref == "." or ref.startswith("/"):
        raise MillefeuilleContractError(f"{label} cannot be derived canonically")
    return ref


def _read_artifact_json(
    context: _StatusContext,
    record: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    target = _artifact_target(context, record, label=label)
    if context.reader is not None:
        payload = _load_reader_json(context.reader, target, label)
    else:
        payload = _load_path_json(target, label)
        if context.portable_snapshots is not None:
            digest = _digest_object(payload)
            prior = context.portable_snapshots.setdefault(target, digest)
            if prior != digest:
                raise MillefeuilleContractError(
                    f"{label} changed during canonical observation"
                )
    _reject_private_payload_markers(payload, label)
    return payload


def _revalidate_portable_artifacts(context: _StatusContext) -> None:
    if context.portable_snapshots is None:
        return
    for path, expected in sorted(
        context.portable_snapshots.items(),
        key=lambda item: item[0].as_posix(),
    ):
        current = _digest_object(_load_path_json(path, "canonical status artifact"))
        if not hmac.compare_digest(current, expected):
            raise MillefeuilleContractError(
                "canonical status artifacts changed during observation"
            )


def _validate_observation(payload: dict[str, Any]) -> None:
    _reject_observation_secrets(payload)
    _require_exact_fields(payload, _OBSERVATION_FIELDS, "status observation")
    if payload.get("schema_version") != STATUS_OBSERVATION_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "status observation schema_version is unsupported"
        )
    kind = _required_string(payload.get("kind"), "status observation kind")
    if kind not in OBSERVATION_KINDS:
        raise MillefeuilleContractError("status observation kind is unsupported")
    authority = _required_object(payload.get("authority"), "observation authority")
    _require_exact_fields(authority, _AUTHORITY_FIELDS, "observation authority")
    if (
        authority.get("source") != STATUS_OBSERVATION_SOURCE
        or authority.get("authoritative") is not False
    ):
        raise MillefeuilleContractError(
            "status observation must be a non-authoritative local observation"
        )
    for field_name in ("paper_id", "run_id"):
        value = _required_string(payload.get(field_name), field_name)
        if _SAFE_ID_RE.fullmatch(value) is None:
            raise MillefeuilleContractError("status observation identity is invalid")
    source_hash = _required_string(payload.get("source_hash"), "source_hash")
    if _SOURCE_HASH_RE.fullmatch(source_hash) is None:
        raise MillefeuilleContractError("status observation source_hash is invalid")
    summary = _required_object(payload.get("summary"), "observation summary")
    _validate_observation_summary(kind, summary)
    integrity = _required_object(payload.get("integrity"), "observation integrity")
    _require_exact_fields(integrity, _INTEGRITY_FIELDS, "observation integrity")
    if integrity.get("algorithm") != "sha256":
        raise MillefeuilleContractError("status observation integrity is invalid")
    supplied = _required_string(
        integrity.get("content_digest"),
        "observation integrity.content_digest",
    )
    if _DIGEST_RE.fullmatch(supplied) is None:
        raise MillefeuilleContractError("status observation digest is invalid")
    expected = compute_status_observation_digest(payload)
    if not hmac.compare_digest(supplied, expected):
        raise MillefeuilleContractError("status observation digest mismatch")


def _validate_observation_summary(kind: str, summary: dict[str, Any]) -> None:
    if kind == "zotero-live-state":
        _require_exact_fields(
            summary,
            frozenset({"status", "item_version", "tag_state", "note_state"}),
            "Zotero observation summary",
        )
        _require_enum(
            summary.get("status"),
            {"consistent", "drifted", "needs-review"},
            "Zotero observation status",
        )
        _required_integer(summary.get("item_version"), "item_version")
        for name in ("tag_state", "note_state"):
            _require_enum(
                summary.get(name),
                {"consistent", "drifted", "not-observed"},
                name,
            )
        return
    if kind == "provider-usage":
        _require_exact_fields(
            summary,
            frozenset(
                {
                    "status",
                    "record_digest",
                    "call_count",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                }
            ),
            "provider observation summary",
        )
        _require_enum(
            summary.get("status"),
            {
                "within-approved-limits",
                "exceeded-approved-limits",
                "needs-review",
            },
            "provider observation status",
        )
        for name in (
            "call_count",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            _required_integer(summary.get(name), name)
        if summary["total_tokens"] != (
            summary["input_tokens"] + summary["output_tokens"]
        ):
            raise MillefeuilleContractError(
                "provider observation token counts must reconcile"
            )
        if (
            _DIGEST_RE.fullmatch(
                _required_string(summary.get("record_digest"), "record_digest")
            )
            is None
        ):
            raise MillefeuilleContractError(
                "provider observation record_digest must be a SHA-256 digest"
            )
        return
    if kind == "index-ledger":
        _require_exact_fields(
            summary,
            frozenset({"status", "lanes", "index_status_digest"}),
            "index-ledger observation summary",
        )
        _require_enum(
            summary.get("status"),
            {"consistent", "drifted", "needs-review"},
            "index-ledger observation status",
        )
        lanes = summary.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            raise MillefeuilleContractError(
                "index-ledger observation lanes must be a non-empty array"
            )
        ordering: list[str] = []
        for lane in lanes:
            value = _required_object(lane, "index-ledger lane")
            _require_exact_fields(
                value,
                frozenset({"lane", "status"}),
                "index-ledger lane",
            )
            lane_name = _require_enum(
                value.get("lane"),
                {"openkb", "pageindex", "condb", "chatindex", "other"},
                "index-ledger lane name",
            )
            ordering.append(lane_name)
            _require_enum(
                value.get("status"),
                {"written"},
                "index-ledger lane status",
            )
        if ordering != sorted(set(ordering)):
            raise MillefeuilleContractError(
                "index-ledger observation lanes must be sorted and unique"
            )
        if (
            _DIGEST_RE.fullmatch(
                _required_string(
                    summary.get("index_status_digest"),
                    "index_status_digest",
                )
            )
            is None
        ):
            raise MillefeuilleContractError(
                "index-ledger index_status_digest must be a SHA-256 digest"
            )
        return
    if kind == "quality":
        _require_exact_fields(
            summary,
            frozenset(
                {
                    "status",
                    "checks_total",
                    "checks_passed",
                    "checks_failed",
                    "record_digest",
                }
            ),
            "quality observation summary",
        )
        _require_enum(
            summary.get("status"),
            {"passed", "needs-review", "failed"},
            "quality observation status",
        )
        total = _required_integer(summary.get("checks_total"), "checks_total")
        passed = _required_integer(summary.get("checks_passed"), "checks_passed")
        failed = _required_integer(summary.get("checks_failed"), "checks_failed")
        if passed + failed != total:
            raise MillefeuilleContractError(
                "quality observation check counts must reconcile"
            )
        if (
            _DIGEST_RE.fullmatch(
                _required_string(summary.get("record_digest"), "record_digest")
            )
            is None
        ):
            raise MillefeuilleContractError(
                "quality observation record_digest must be a SHA-256 digest"
            )
        return
    _require_exact_fields(
        summary,
        frozenset(
            {
                "status",
                "operation_count",
                "receipt_digest",
                "audit_digest",
                "record_digest",
            }
        ),
        "writeback observation summary",
    )
    _require_enum(
        summary.get("status"),
        {"written", "skipped", "failed", "needs-review"},
        "writeback observation status",
    )
    _required_integer(summary.get("operation_count"), "operation_count")
    for name in ("receipt_digest", "audit_digest", "record_digest"):
        value = _required_string(summary.get(name), name)
        if _DIGEST_RE.fullmatch(value) is None:
            raise MillefeuilleContractError(
                "writeback observation identities must be SHA-256 digests"
            )


def _validate_stage_manifest_payload(payload: dict[str, Any]) -> None:
    _require_exact_fields(payload, _STAGE_TOP_FIELDS, "stage manifest")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise MillefeuilleContractError("stage manifest stages must not be empty")
    names: list[str] = []
    for raw_record in stages:
        record = _required_object(raw_record, "stage record")
        if set(record) - _STAGE_RECORD_FIELDS:
            raise MillefeuilleContractError("stage record contains unknown fields")
        required = {"name", "status", "inputs", "outputs", "manual_gate_required"}
        if not required.issubset(record):
            raise MillefeuilleContractError("stage record is missing required fields")
        name = _required_string(record.get("name"), "stage name")
        names.append(name)
        if not isinstance(record.get("manual_gate_required"), bool):
            raise MillefeuilleContractError(
                "stage manual_gate_required must be boolean"
            )
        for field_name in ("inputs", "outputs", "notes"):
            if field_name not in record:
                continue
            values = record[field_name]
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise MillefeuilleContractError(
                    f"stage {field_name} must be an array of strings"
                )
            if field_name in {"inputs", "outputs"} and len(values) != len(set(values)):
                raise MillefeuilleContractError(f"stage {field_name} must be unique")
    if len(names) != len(set(names)):
        raise MillefeuilleContractError("stage manifest contains duplicate stages")


def _validate_artifact_index_payload(payload: dict[str, Any]) -> None:
    _require_exact_fields(payload, _INDEX_TOP_FIELDS, "artifact index")
    source_pack = _required_object(payload.get("source_pack"), "source_pack")
    if not set(source_pack).issubset(_SOURCE_PACK_INDEX_FIELDS) or not {
        "ref",
        "source_type",
        "source_hash",
    }.issubset(source_pack):
        raise MillefeuilleContractError("artifact index source_pack fields are invalid")
    artifacts = _required_object(payload.get("artifacts"), "artifacts")
    for record in artifacts.values():
        value = _required_object(record, "artifact record")
        if not set(value).issubset(_ARTIFACT_RECORD_FIELDS) or not {
            "kind",
            "ref",
        }.issubset(value):
            raise MillefeuilleContractError("artifact record fields are invalid")
        if "private_content" in value and not isinstance(
            value["private_content"], bool
        ):
            raise MillefeuilleContractError("artifact private_content must be boolean")
    lanes = payload.get("indexes")
    if not isinstance(lanes, list):
        raise MillefeuilleContractError("artifact index lanes must be an array")
    names: list[str] = []
    for lane in lanes:
        value = _required_object(lane, "artifact index lane")
        if not set(value).issubset(_INDEX_LANE_FIELDS) or not {
            "lane",
            "status",
        }.issubset(value):
            raise MillefeuilleContractError("artifact index lane fields are invalid")
        names.append(_required_string(value.get("lane"), "index lane"))
    if len(names) != len(set(names)):
        raise MillefeuilleContractError("artifact index contains duplicate lanes")
    writeback = _required_object(payload.get("zotero_writeback"), "writeback")
    if not set(writeback).issubset(_WRITEBACK_INDEX_FIELDS) or not {
        "mode",
        "status",
    }.issubset(writeback):
        raise MillefeuilleContractError("artifact index writeback fields are invalid")


def _validate_retrieval_index_payload(payload: dict[str, Any]) -> None:
    allowed = frozenset(
        {
            "schema_version",
            "paper_id",
            "run_id",
            "source_hash",
            "selected_fulltext_ref",
            "summary_ref",
            "paper_card_ref",
            "lanes",
            "duplicate_scan",
            "collision_summary",
            "quality_warnings",
        }
    )
    required = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "selected_fulltext_ref",
        "summary_ref",
        "paper_card_ref",
        "lanes",
    }
    if set(payload) - allowed or not required.issubset(payload):
        raise MillefeuilleContractError("retrieval index status fields are invalid")
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        raise MillefeuilleContractError("retrieval index lanes must be an array")
    lane_fields = {
        "lane",
        "status",
        "skip_reason",
        "target",
        "chunking_profile",
        "quality_warnings",
    }
    for lane in lanes:
        value = _required_object(lane, "retrieval index lane")
        if set(value) - lane_fields or not {"lane", "status"}.issubset(value):
            raise MillefeuilleContractError("retrieval index lane fields are invalid")


def _validate_acceptance_payload(payload: dict[str, Any]) -> None:
    allowed = frozenset(
        {
            "schema_version",
            "paper_id",
            "run_id",
            "source_hash",
            "source_pack_ref",
            "status",
            "counts",
            "checks",
            "handoff",
            "duplicate_scan",
            "writeback_preview",
            "review_reasons",
        }
    )
    required = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "source_pack_ref",
        "status",
        "counts",
        "checks",
    }
    if set(payload) - allowed or not required.issubset(payload):
        raise MillefeuilleContractError("acceptance summary fields are invalid")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise MillefeuilleContractError("acceptance checks must be an array")
    allowed_check = {"name", "status", "refs", "notes", "details"}
    for check in checks:
        value = _required_object(check, "acceptance check")
        if set(value) - allowed_check or not {"name", "status", "refs"}.issubset(value):
            raise MillefeuilleContractError("acceptance check fields are invalid")


def _validate_classification_plan_payload(payload: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "run_id",
        "taxonomy_version",
        "mode",
        "papers",
        "default_profile",
    }
    required = {"schema_version", "run_id", "taxonomy_version", "mode", "papers"}
    if set(payload) - allowed or not required.issubset(payload):
        raise MillefeuilleContractError("classification plan fields are invalid")
    papers = payload.get("papers")
    if not isinstance(papers, list) or len(papers) != 1:
        raise MillefeuilleContractError(
            "classification plan must contain exactly one paper"
        )
    paper = _required_object(papers[0], "classification plan paper")
    paper_allowed = {
        "paper_id",
        "decision_ref",
        "decision_markdown_ref",
        "status",
        "writeback_preview_ref",
    }
    if set(paper) - paper_allowed or not {
        "paper_id",
        "decision_ref",
        "status",
    }.issubset(paper):
        raise MillefeuilleContractError("classification plan paper fields are invalid")


def _validate_classification_decision_payload(payload: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "taxonomy_version",
        "mode",
        "status",
        "primary_path",
        "confidence",
        "evidence_refs",
        "strongest_rejected_path",
        "rejected_alternatives",
        "review_reasons",
        "qa_flags",
        "writeback_preview_ref",
    }
    required = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "taxonomy_version",
        "mode",
        "status",
        "primary_path",
        "confidence",
        "evidence_refs",
    }
    if set(payload) - allowed or not required.issubset(payload):
        raise MillefeuilleContractError("classification decision fields are invalid")


def _validate_writeback_plan_payload(payload: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "mode",
        "status",
        "add_tags",
        "remove_tags",
        "note_markdown_ref",
        "destination_collection",
        "classification_ref",
        "execution_notes",
    }
    required = {
        "schema_version",
        "paper_id",
        "run_id",
        "source_hash",
        "mode",
        "status",
        "add_tags",
        "remove_tags",
    }
    if set(payload) - allowed or not required.issubset(payload):
        raise MillefeuilleContractError("writeback plan fields are invalid")


def _validate_completion_gate_payload(
    payload: dict[str, Any],
    *,
    context: _StatusContext,
) -> None:
    _require_exact_fields(payload, _COMPLETION_GATE_FIELDS, "completion gate result")
    if payload.get("schema_version") != _COMPLETION_GATE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "completion gate result schema_version is unsupported"
        )
    _require_record_identity(
        paper_id=_required_string(payload.get("paper_id"), "paper_id"),
        run_id=_required_string(payload.get("run_id"), "run_id"),
        source_hash=_required_string(payload.get("source_hash"), "source_hash"),
        context=context,
        label="completion gate result",
    )
    _require_enum(
        payload.get("status"),
        {"passed", "needs-review", "failed"},
        "completion gate status",
    )
    total = _required_integer(payload.get("checks_total"), "checks_total")
    passed = _required_integer(payload.get("checks_passed"), "checks_passed")
    failed = _required_integer(payload.get("checks_failed"), "checks_failed")
    if passed + failed != total:
        raise MillefeuilleContractError(
            "completion gate result check counts must reconcile"
        )
    _validate_content_integrity(payload, label="completion gate result")


def _validate_writeback_result_payload(
    payload: dict[str, Any],
    *,
    context: _StatusContext,
) -> None:
    _require_exact_fields(payload, _WRITEBACK_RESULT_FIELDS, "writeback result")
    if payload.get("schema_version") != _WRITEBACK_RESULT_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "writeback result schema_version is unsupported"
        )
    _require_record_identity(
        paper_id=_required_string(payload.get("paper_id"), "paper_id"),
        run_id=_required_string(payload.get("run_id"), "run_id"),
        source_hash=_required_string(payload.get("source_hash"), "source_hash"),
        context=context,
        label="writeback result",
    )
    if payload.get("mode") != "approved-live":
        raise MillefeuilleContractError("writeback result mode must be approved-live")
    _require_enum(
        payload.get("status"),
        {"written", "skipped", "failed", "needs-review"},
        "writeback result status",
    )
    source_item_version = _required_integer(
        payload.get("source_item_version"),
        "source_item_version",
    )
    item_version = _required_integer(payload.get("item_version"), "item_version")
    expected_source_version = _canonical_source_zotero_version(
        context.source_pack_payload,
        required=True,
    )
    if source_item_version != expected_source_version:
        raise MillefeuilleContractError(
            "writeback result source_item_version drifts from source-pack identity"
        )
    if payload.get("status") == "written" and item_version < source_item_version:
        raise MillefeuilleContractError(
            "writeback result item_version precedes its source version"
        )
    _required_integer(payload.get("operation_count"), "operation_count")
    for name in ("receipt_digest", "audit_digest"):
        if _DIGEST_RE.fullmatch(_required_string(payload.get(name), name)) is None:
            raise MillefeuilleContractError(
                "writeback result identities must be SHA-256 digests"
            )
    _validate_content_integrity(payload, label="writeback result")


def _validate_content_integrity(payload: dict[str, Any], *, label: str) -> None:
    integrity = _required_object(payload.get("integrity"), f"{label} integrity")
    _require_exact_fields(integrity, _INTEGRITY_FIELDS, f"{label} integrity")
    if integrity.get("algorithm") != "sha256":
        raise MillefeuilleContractError(f"{label} integrity algorithm is invalid")
    supplied = _required_string(
        integrity.get("content_digest"),
        f"{label} integrity.content_digest",
    )
    if _DIGEST_RE.fullmatch(supplied) is None:
        raise MillefeuilleContractError(f"{label} content digest is invalid")
    body = dict(payload)
    body.pop("integrity", None)
    expected = _digest_object(body)
    if not hmac.compare_digest(supplied, expected):
        raise MillefeuilleContractError(f"{label} content digest mismatch")


def _require_identity(
    artifact_index: ArtifactIndex,
    *,
    paper_id: str,
    run_id: str,
    source_hash: str,
) -> None:
    if artifact_index.paper_id != paper_id:
        raise MillefeuilleContractError("artifact index paper_id drift")
    if artifact_index.run_id != run_id:
        raise MillefeuilleContractError("artifact index run_id drift")
    if artifact_index.source_pack.get("source_hash") != source_hash:
        raise MillefeuilleContractError("artifact index source_hash drift")


def _require_stage_index_match(
    stage_manifest: StageManifest,
    artifact_index: ArtifactIndex,
) -> None:
    if stage_manifest.run_id != artifact_index.run_id:
        raise MillefeuilleContractError("stage manifest run_id drift")
    stage_statuses = stage_status_map(stage_manifest)
    index_statuses = {
        name: str(record["status"]) for name, record in artifact_index.stages.items()
    }
    if stage_statuses != index_statuses:
        raise MillefeuilleContractError(
            "stage manifest and artifact index status maps drift"
        )


def _require_record_identity(
    *,
    paper_id: str,
    run_id: str,
    source_hash: str,
    context: _StatusContext,
    label: str,
) -> None:
    if (
        paper_id != context.paper_id
        or run_id != context.run_id
        or source_hash != context.source_hash
    ):
        raise MillefeuilleContractError(f"{label} identity drift")


def _require_canonical_refs(
    artifact_index: ArtifactIndex,
    *,
    stage_manifest: StageManifest,
    run_dir: Path,
    source_pack_dir: Path,
) -> None:
    if not _same_lexical_path(Path(artifact_index.artifact_root), run_dir):
        raise MillefeuilleContractError(
            "artifact index artifact_root does not name the canonical run"
        )
    if not _same_lexical_path(
        Path(str(artifact_index.source_pack["ref"])),
        source_pack_dir,
    ):
        raise MillefeuilleContractError(
            "artifact index source_pack.ref does not name the canonical source pack"
        )
    manifest_ref = artifact_index.source_pack.get("manifest_ref")
    if not isinstance(manifest_ref, str):
        raise MillefeuilleContractError(
            "canonical artifact index must bind source_pack.manifest_ref"
        )
    expected_manifest_ref = _expected_relative_ref(
        source_pack_dir / "manifest.json",
        start=run_dir,
        label="source-pack manifest ref",
    )
    if manifest_ref != expected_manifest_ref:
        raise MillefeuilleContractError("source-pack manifest_ref drift")
    stage_record = artifact_index.artifacts.get("stage_manifest")
    if stage_record is None:
        raise MillefeuilleContractError(
            "canonical artifact index must bind its stage manifest"
        )
    if (
        stage_record.get("kind") != "stage-manifest"
        or stage_record.get("format") != "json"
        or stage_record.get("private_content") is not False
    ):
        raise MillefeuilleContractError(
            "canonical stage manifest artifact record is invalid"
        )
    stage_ref = _required_string(stage_record.get("ref"), "stage manifest ref")
    stage_path = _resolve_artifact_ref(
        stage_ref,
        run_dir=run_dir,
        boundary=source_pack_dir,
        label="stage manifest ref",
    )
    if not _same_lexical_path(stage_path, run_dir / "stage-manifest.json"):
        raise MillefeuilleContractError("stage manifest ref drift")
    owning_stage = _required_string(
        stage_record.get("stage"),
        "stage manifest artifact stage",
    )
    matches = [
        record for record in stage_manifest.stages if record.name.value == owning_stage
    ]
    if len(matches) != 1 or stage_ref not in matches[0].outputs:
        raise MillefeuilleContractError(
            "stage manifest ref is not claimed by its canonical stage output"
        )


def _resolve_artifact_ref(
    ref: str,
    *,
    run_dir: Path,
    boundary: Path,
    label: str,
) -> Path:
    if (
        not isinstance(ref, str)
        or not ref
        or len(ref) > 512
        or ref != ref.strip()
        or "\\" in ref
        or "\x00" in ref
        or re.match(r"[A-Za-z]:", ref) is not None
    ):
        raise MillefeuilleContractError(
            f"{label} must be a portable bounded relative ref"
        )
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or pure.as_posix() != ref
        or len(pure.parts) > 32
        or any(
            part in {"", ".", ".."} or _SAFE_REF_PART_RE.fullmatch(part) is None
            for part in pure.parts
        )
    ):
        raise MillefeuilleContractError(
            f"{label} must be a portable bounded relative ref"
        )
    target = Path(os.path.abspath(run_dir.joinpath(*pure.parts)))
    allowed = Path(os.path.abspath(boundary))
    try:
        common = os.path.commonpath((os.fspath(target), os.fspath(allowed)))
    except ValueError as exc:
        raise MillefeuilleContractError(f"{label} escapes its canonical root") from exc
    if os.path.normcase(common) != os.path.normcase(os.fspath(allowed)):
        raise MillefeuilleContractError(f"{label} escapes its canonical root")
    return target


def _same_lexical_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _validated_operator_path(path: str | Path, *, label: str) -> Path:
    target = Path(path)
    if not target.parts or any(part == ".." for part in target.parts):
        raise MillefeuilleContractError(
            f"{label} path must not contain parent traversal"
        )
    return target.absolute()


def _portable_directory_snapshot(
    path: Path,
    *,
    label: str,
) -> tuple[tuple[int, int, int, int, int], ...]:
    target = path.absolute()
    anchor = Path(target.anchor)
    current = anchor
    snapshots: list[tuple[int, int, int, int, int]] = []
    for part in target.parts[1:]:
        if part in {"", "."}:
            continue
        if part == "..":
            raise MillefeuilleContractError(
                f"{label} path must not contain parent traversal"
            )
        current /= part
        try:
            value = os.lstat(current)
        except OSError as exc:
            raise MillefeuilleContractError(f"could not inspect {label}") from exc
        attributes = int(getattr(value, "st_file_attributes", 0))
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or attributes & _WINDOWS_REPARSE_POINT
        ):
            raise MillefeuilleContractError(
                f"{label} must not contain links, reparse points, or non-directories"
            )
        snapshots.append(
            (
                int(value.st_dev),
                int(value.st_ino),
                int(value.st_mode),
                int(getattr(value, "st_ctime_ns", value.st_ctime * 1_000_000_000)),
                attributes,
            )
        )
    return tuple(snapshots)


def _load_reader_json(
    reader: RootArtifactReader,
    path: str | Path,
    label: str,
) -> dict[str, Any]:
    return _load_strict_json_bytes(
        reader.read_bytes(path, label, max_bytes=_CANONICAL_JSON_MAX_BYTES),
        label,
    )


def _load_path_json(path: str | Path, label: str) -> dict[str, Any]:
    return _load_strict_json_bytes(
        read_bytes_no_follow(path, label, max_bytes=_CANONICAL_JSON_MAX_BYTES),
        label,
    )


def _probe_path_json(path: Path, label: str) -> dict[str, Any] | None:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MillefeuilleContractError(f"could not inspect {label}") from exc
    if stat.S_ISLNK(value.st_mode) or (
        getattr(value, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    ):
        raise MillefeuilleContractError(f"{label} must not be a link or reparse point")
    return _load_path_json(path, label)


def _load_strict_json_bytes(payload_bytes: bytes, label: str) -> dict[str, Any]:
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid UTF-8") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_finite_json_number,
        )
    except MillefeuilleContractError:
        raise
    except (ValueError, OverflowError, RecursionError) as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    _validate_json_tree(payload, label=label)
    return payload


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MillefeuilleContractError("status JSON contains duplicate fields")
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> None:
    del value
    raise MillefeuilleContractError("status JSON contains a non-finite number")


def _reject_observation_secrets(value: object) -> None:
    _validate_json_tree(
        value,
        label="status observation",
        reject_observation_secrets=True,
    )


def _reject_private_payload_markers(value: object, label: str) -> None:
    _validate_json_tree(
        value,
        label=label,
        reject_private_markers=True,
    )


def _validate_json_tree(
    value: object,
    *,
    label: str,
    reject_observation_secrets: bool = False,
    reject_private_markers: bool = False,
) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    seen_containers: set[int] = set()
    node_count = 0
    while stack:
        current, depth = stack.pop()
        node_count += 1
        if node_count > _JSON_MAX_NODES:
            raise MillefeuilleContractError(f"{label} exceeds the JSON node limit")
        if depth > _JSON_MAX_DEPTH:
            raise MillefeuilleContractError(f"{label} exceeds the JSON depth limit")
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, int):
            if abs(current) > _JSON_SAFE_INTEGER_MAX:
                raise MillefeuilleContractError(f"{label} contains an unsafe integer")
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise MillefeuilleContractError(f"{label} contains a non-finite number")
            continue
        if isinstance(current, str):
            markers = (
                _FORBIDDEN_VALUE_MARKERS
                if reject_observation_secrets
                else _FORBIDDEN_VALUE_MARKERS[:-1]
                if reject_private_markers
                else ()
            )
            if any(marker.search(current) for marker in markers):
                raise MillefeuilleContractError(
                    f"{label} contains credential, URL, or private payload material"
                )
            continue
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen_containers:
                raise MillefeuilleContractError(
                    f"{label} contains a cyclic or reused JSON container"
                )
            seen_containers.add(identity)
            for key, nested in current.items():
                if not isinstance(key, str):
                    raise MillefeuilleContractError(
                        f"{label} contains a non-string JSON field"
                    )
                if (
                    reject_observation_secrets
                    and key.casefold() in _FORBIDDEN_OBSERVATION_FIELDS
                ):
                    raise MillefeuilleContractError(
                        "status observation contains a forbidden private field"
                    )
                stack.append((nested, depth + 1))
            continue
        if isinstance(current, list):
            identity = id(current)
            if identity in seen_containers:
                raise MillefeuilleContractError(
                    f"{label} contains a cyclic or reused JSON container"
                )
            seen_containers.add(identity)
            stack.extend((nested, depth + 1) for nested in current)
            continue
        raise MillefeuilleContractError(f"{label} contains a non-JSON value")


def _require_exact_fields(
    value: dict[str, Any],
    expected: Collection[str],
    label: str,
) -> None:
    actual = set(value)
    expected_set = set(expected)
    if actual != expected_set:
        raise MillefeuilleContractError(f"{label} fields must match the strict schema")


def _required_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MillefeuilleContractError(f"{label} must be a non-empty canonical string")
    return value


def _required_integer(value: object, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _JSON_SAFE_INTEGER_MAX
    ):
        raise MillefeuilleContractError(
            f"{label} must be a non-negative JSON-safe integer"
        )
    return value


def _require_enum(value: object, allowed: Collection[str], label: str) -> str:
    text = _required_string(value, label)
    if text not in allowed:
        raise MillefeuilleContractError(f"{label} is unsupported")
    return text


def _digest_object(value: object) -> str:
    _validate_json_tree(value, label="status digest input")
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "status digest input is not canonical JSON"
        ) from exc
    return "sha256:" + hashlib.sha256(serialized).hexdigest()
