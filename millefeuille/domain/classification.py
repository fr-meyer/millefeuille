"""Offline classification orchestration for accepted Millefeuille runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.millefeuille import (
    ClassificationActionOutcome,
    ClassificationActionRecord,
    ClassificationBatchRunRecord,
    ClassificationBatchSummaryRecord,
    ClassificationDecisionRecord,
    ClassificationMode,
    ClassificationPlanRecord,
    ClassificationStatus,
    MillefeuilleContractError,
    RejectedAlternativeRecord,
    StageName,
    StageStatus,
)
from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
from millefeuille.domain.stage_runtime import (
    ResolvedRunArtifacts,
    load_json_object,
    load_jsonl_records,
    persist_run_artifacts,
    relative_ref,
    resolve_run_artifacts,
    update_artifact_index,
    upsert_artifact_record,
    upsert_stage_record,
    write_json_object,
    write_jsonl_records,
    write_text,
)

CLASSIFICATION_EVIDENCE_SCHEMA_VERSION = (
    "millefeuille-classification-fixture-evidence/v0.1"
)
CLASSIFICATION_DIR_REF = Path("classification")
CLASSIFICATION_PLAN_REF = CLASSIFICATION_DIR_REF / "classification-plan.json"
DECISION_DIR_REF = CLASSIFICATION_DIR_REF / "decision-records"
REJECTED_ALTERNATIVES_REF = CLASSIFICATION_DIR_REF / "rejected-alternatives.jsonl"
ADJUDICATION_QUEUE_REF = CLASSIFICATION_DIR_REF / "adjudication-queue.jsonl"
TAXONOMY_CHANGE_REQUESTS_REF = CLASSIFICATION_DIR_REF / "taxonomy-change-requests.jsonl"
WRITEBACK_PREVIEW_REF = CLASSIFICATION_DIR_REF / "zotero-writeback-preview.json"
BATCH_REPORT_REF = CLASSIFICATION_DIR_REF / "batch-classification-report.md"
CLASSIFICATION_BATCH_ROOT_REF = Path("batches/millefeuille")
CLASSIFICATION_BATCH_SUMMARY_REF = Path(
    "classification/batch-classification-summary.json"
)
CLASSIFICATION_BATCH_REPORT_REF = Path(
    "classification/batch-classification-report.md"
)
CLASSIFICATION_BATCH_MANIFEST_SCHEMA = (
    "millefeuille-classification-batch-manifest/v0.1"
)
CLASSIFICATION_ACTION_EVIDENCE_SCHEMA_VERSION = (
    "millefeuille-classification-action-evidence/v0.1"
)
CLASSIFICATION_ACTION_ROOT_REF = CLASSIFICATION_DIR_REF / "actions"
CLASSIFICATION_ACTION_RECORD_REF = Path("action-record.json")
CLASSIFICATION_ACTION_MARKDOWN_REF = Path("action-record.md")
CLASSIFICATION_ACTION_FINAL_DECISION_REF = Path("final-decision.json")
CLASSIFICATION_ACTION_FINAL_MARKDOWN_REF = Path("final-decision.md")
CLASSIFICATION_ACTION_PREVIEW_REF = Path("zotero-writeback-preview.json")
CLASSIFICATION_ACTION_ADJUDICATION_QUEUE_REF = Path("adjudication-queue.jsonl")
CLASSIFICATION_ACTION_TAXONOMY_REQUESTS_REF = Path(
    "taxonomy-change-requests.jsonl"
)
_SAFE_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_BATCH_LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_SAFE_EVIDENCE_REF_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")


@dataclass(frozen=True)
class ClassificationWriteResult:
    paper_id: str
    run_id: str
    status: str
    plan_path: Path
    decision_json_path: Path
    writeback_preview_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "plan_path": str(self.plan_path),
            "decision_json_path": str(self.decision_json_path),
            "writeback_preview_path": str(self.writeback_preview_path),
        }


@dataclass(frozen=True)
class ClassificationBatchWriteResult:
    batch_id: str
    taxonomy_version: str
    status: str
    counts: dict[str, int]
    routes: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    summary_path: Path
    report_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "taxonomy_version": self.taxonomy_version,
            "status": self.status,
            "counts": dict(self.counts),
            "routes": [dict(route) for route in self.routes],
            "runs": [dict(run) for run in self.runs],
            "summary_path": str(self.summary_path),
            "report_path": str(self.report_path),
        }


@dataclass(frozen=True)
class ClassificationActionWriteResult:
    action_id: str
    paper_id: str
    run_id: str
    mode: str
    outcome: str
    status: str
    action_record_path: Path
    final_decision_path: Path
    writeback_preview_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "outcome": self.outcome,
            "status": self.status,
            "action_record_path": str(self.action_record_path),
            "final_decision_path": str(self.final_decision_path),
            "writeback_preview_path": str(self.writeback_preview_path),
        }


@dataclass(frozen=True)
class _PreparedClassification:
    resolved: ResolvedRunArtifacts
    decision: ClassificationDecisionRecord
    plan: ClassificationPlanRecord
    preview_payload: dict[str, Any]
    taxonomy_records: list[dict[str, Any]]
    plan_path: Path
    decision_json_path: Path
    decision_markdown_path: Path
    rejected_path: Path
    adjudication_queue_path: Path
    taxonomy_requests_path: Path
    preview_path: Path
    batch_report_path: Path


@dataclass(frozen=True)
class _PreparedClassificationAction:
    resolved: ResolvedRunArtifacts
    action: ClassificationActionRecord
    final_decision: ClassificationDecisionRecord
    plan: ClassificationPlanRecord
    preview_payload: dict[str, Any]
    taxonomy_records: list[dict[str, Any]]
    adjudication_records: list[dict[str, Any]]
    action_record_path: Path
    action_markdown_path: Path
    final_decision_path: Path
    final_markdown_path: Path
    action_preview_path: Path
    canonical_preview_path: Path
    taxonomy_requests_path: Path
    adjudication_queue_path: Path
    plan_path: Path


def write_classification_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    default_profile: str | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> ClassificationWriteResult:
    prepared = _prepare_classification_from_evidence(
        evidence_path=evidence_path,
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        default_profile=default_profile,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    return _persist_classification(prepared)


def write_classification_action_from_evidence(
    *,
    action_evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    default_profile: str | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> ClassificationActionWriteResult:
    """Materialize one deterministic offline review or adjudication action."""

    prepared = _prepare_classification_action_from_evidence(
        action_evidence_path=action_evidence_path,
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        default_profile=default_profile,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    _preflight_existing_action(prepared)
    return _persist_classification_action(prepared)


def write_classification_batch_summary(
    *,
    source_pack_root: str | Path,
    batch_manifest_path: str | Path,
    default_profile: str | None = None,
) -> ClassificationBatchWriteResult:
    """Classify a preflighted set of accepted offline runs deterministically."""

    manifest_path = Path(batch_manifest_path)
    batch_id, taxonomy_version, run_locators = (
        _load_classification_batch_manifest(manifest_path)
    )
    prepared: list[_PreparedClassification] = []
    for locator in run_locators:
        item = _prepare_classification_from_evidence(
            evidence_path=manifest_path.parent / locator["evidence_ref"],
            source_pack_root=source_pack_root,
            run_id=locator["run_id"],
            paper_id=locator.get("paper_id"),
            item_key=locator.get("item_key"),
            default_profile=default_profile,
        )
        if item.decision.taxonomy_version != taxonomy_version:
            raise MillefeuilleContractError(
                "classification batch taxonomy drift for "
                f"{item.resolved.paper_id}/{item.resolved.run_id}: expected "
                f"{taxonomy_version!r}, got {item.decision.taxonomy_version!r}"
            )
        if item.decision.mode != ClassificationMode.BATCH:
            raise MillefeuilleContractError(
                "classification batch evidence mode must be 'batch' for "
                f"{item.resolved.paper_id}/{item.resolved.run_id}"
            )
        prepared.append(item)

    prepared.sort(
        key=lambda item: (item.resolved.paper_id, item.resolved.run_id)
    )
    identities = [
        (item.resolved.paper_id, item.resolved.run_id) for item in prepared
    ]
    if len(identities) != len(set(identities)):
        raise MillefeuilleContractError(
            "classification batch runs must have unique paper_id/run_id pairs"
        )

    root = Path(source_pack_root)
    run_records = [
        ClassificationBatchRunRecord(
            paper_id=item.resolved.paper_id,
            run_id=item.resolved.run_id,
            source_hash=item.resolved.source_hash,
            taxonomy_version=item.decision.taxonomy_version,
            status=item.decision.status,
            primary_path=item.decision.primary_path,
            decision_ref=relative_ref(item.decision_json_path, root),
            writeback_preview_ref=relative_ref(item.preview_path, root),
            review_reasons=list(item.decision.review_reasons),
        )
        for item in prepared
    ]
    counts = {
        "runs": len(run_records),
        "classified": sum(
            run.status == ClassificationStatus.CLASSIFIED for run in run_records
        ),
        "needs_review": sum(
            run.status == ClassificationStatus.NEEDS_REVIEW for run in run_records
        ),
        "adjudication_required": sum(
            run.status == ClassificationStatus.ADJUDICATION_REQUIRED
            for run in run_records
        ),
    }
    status = (
        ClassificationStatus.ADJUDICATION_REQUIRED
        if counts["adjudication_required"]
        else ClassificationStatus.NEEDS_REVIEW
        if counts["needs_review"]
        else ClassificationStatus.CLASSIFIED
    )
    routes = _build_classification_batch_routes(run_records)
    summary = ClassificationBatchSummaryRecord(
        batch_id=batch_id,
        taxonomy_version=taxonomy_version,
        status=status,
        counts=counts,
        routes=routes,
        runs=run_records,
    )

    for item in prepared:
        _persist_classification(item)

    batch_dir = root / CLASSIFICATION_BATCH_ROOT_REF / batch_id
    summary_path = batch_dir / CLASSIFICATION_BATCH_SUMMARY_REF
    report_path = batch_dir / CLASSIFICATION_BATCH_REPORT_REF
    write_json_object(summary_path, summary.to_dict())
    write_text(report_path, _render_classification_batch_markdown(summary))
    return ClassificationBatchWriteResult(
        batch_id=batch_id,
        taxonomy_version=taxonomy_version,
        status=summary.status.value,
        counts=dict(summary.counts),
        routes=[dict(route) for route in summary.routes],
        runs=[run.to_dict() for run in summary.runs],
        summary_path=summary_path,
        report_path=report_path,
    )


def _prepare_classification_action_from_evidence(
    *,
    action_evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None,
    item_key: str | None,
    default_profile: str | None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> _PreparedClassificationAction:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    _require_passing_acceptance(resolved)
    evidence = _load_classification_action_evidence(action_evidence_path)
    action_id = str(evidence["action_id"])
    mode = ClassificationMode(str(evidence["mode"]))
    outcome = ClassificationActionOutcome(str(evidence["outcome"]))
    prior_path = _resolve_safe_run_file_ref(
        ref=str(evidence["prior_decision_ref"]),
        run_dir=resolved.run_dir,
        label="classification action prior_decision_ref",
    )
    try:
        prior = ClassificationDecisionRecord.from_dict(
            load_json_object(prior_path, "prior classification decision")
        )
    except MillefeuilleContractError:
        raise
    except (AttributeError, TypeError) as exc:
        raise MillefeuilleContractError(
            "prior classification decision has invalid field types"
        ) from exc
    expected_identity = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "source_hash": resolved.source_hash,
    }
    for field_name, expected in expected_identity.items():
        actual = getattr(prior, field_name)
        if actual != expected:
            raise MillefeuilleContractError(
                f"prior classification decision {field_name} drift: "
                f"expected {expected!r}, got {actual!r}"
            )
    taxonomy_version = str(evidence["taxonomy_version"])
    if prior.taxonomy_version != taxonomy_version:
        raise MillefeuilleContractError(
            "classification action taxonomy drift: expected prior decision "
            f"taxonomy {prior.taxonomy_version!r}, got {taxonomy_version!r}"
        )
    if mode == ClassificationMode.ADJUDICATE and prior.status not in {
        ClassificationStatus.NEEDS_REVIEW,
        ClassificationStatus.ADJUDICATION_REQUIRED,
    }:
        raise MillefeuilleContractError(
            "adjudication requires a needs-review or adjudication-required "
            "prior decision"
        )

    primary_path = str(evidence["primary_path"])
    if outcome in {
        ClassificationActionOutcome.NO_CHANGE,
        ClassificationActionOutcome.CONFIRMED,
    } and primary_path != prior.primary_path:
        raise MillefeuilleContractError(
            f"{outcome.value} action must retain prior primary_path "
            f"{prior.primary_path!r}"
        )
    unresolved = outcome in {
        ClassificationActionOutcome.ESCALATED,
        ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED,
    }
    review_reasons = list(evidence.get("review_reasons", []))
    if unresolved and not review_reasons:
        raise MillefeuilleContractError(
            f"{outcome.value} action requires non-empty review_reasons"
        )
    if not unresolved and review_reasons:
        raise MillefeuilleContractError(
            f"resolved {outcome.value} action must not retain review_reasons"
        )
    taxonomy_request = evidence.get("taxonomy_change_request")
    requires_taxonomy_request = (
        outcome == ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED
    )
    if requires_taxonomy_request != isinstance(taxonomy_request, dict):
        raise MillefeuilleContractError(
            "taxonomy-change-requested requires exactly one "
            "taxonomy_change_request object"
        )

    evidence_refs = _normalize_action_evidence_refs(
        evidence_refs=list(evidence["evidence_refs"]),
        run_dir=resolved.run_dir,
    )
    action_dir = resolved.run_dir / CLASSIFICATION_ACTION_ROOT_REF / action_id
    action_record_path = action_dir / CLASSIFICATION_ACTION_RECORD_REF
    action_markdown_path = action_dir / CLASSIFICATION_ACTION_MARKDOWN_REF
    final_decision_path = action_dir / CLASSIFICATION_ACTION_FINAL_DECISION_REF
    final_markdown_path = action_dir / CLASSIFICATION_ACTION_FINAL_MARKDOWN_REF
    action_preview_path = action_dir / CLASSIFICATION_ACTION_PREVIEW_REF
    canonical_preview_path = resolved.run_dir / WRITEBACK_PREVIEW_REF
    taxonomy_requests_path = (
        action_dir / CLASSIFICATION_ACTION_TAXONOMY_REQUESTS_REF
    )
    adjudication_queue_path = (
        action_dir / CLASSIFICATION_ACTION_ADJUDICATION_QUEUE_REF
    )
    status = (
        ClassificationStatus.ADJUDICATION_REQUIRED
        if unresolved
        else ClassificationStatus.CLASSIFIED
    )
    rejected_alternatives: list[RejectedAlternativeRecord] = []
    for record in evidence.get("rejected_alternatives", []):
        alternative = RejectedAlternativeRecord.from_dict(record)
        rejected_alternatives.append(
            RejectedAlternativeRecord(
                path=alternative.path,
                reason=alternative.reason,
                evidence_refs=(
                    _normalize_action_evidence_refs(
                        evidence_refs=alternative.evidence_refs,
                        run_dir=resolved.run_dir,
                    )
                    if alternative.evidence_refs
                    else []
                ),
            )
        )
    final_decision = ClassificationDecisionRecord(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        taxonomy_version=taxonomy_version,
        mode=mode,
        status=status,
        primary_path=primary_path,
        confidence=str(evidence["confidence"]),
        evidence_refs=evidence_refs,
        strongest_rejected_path=evidence.get("strongest_rejected_path"),
        rejected_alternatives=rejected_alternatives,
        review_reasons=review_reasons,
        qa_flags=list(evidence.get("qa_flags", [])),
        writeback_preview_ref=relative_ref(
            action_preview_path,
            resolved.run_dir,
        ),
    )
    preview_config = evidence.get("writeback_preview") or {}
    if unresolved and "millefeuille-classified" in preview_config.get(
        "add_tags", []
    ):
        raise MillefeuilleContractError(
            "unresolved classification action must not preview the "
            "millefeuille-classified tag"
        )
    preview_payload = _build_writeback_preview(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        evidence=evidence,
        classified=not unresolved,
    )
    taxonomy_records: list[dict[str, Any]] = []
    taxonomy_request_ref: str | None = None
    if requires_taxonomy_request:
        assert isinstance(taxonomy_request, dict)
        request_evidence_refs = _normalize_action_evidence_refs(
            evidence_refs=list(taxonomy_request.get("evidence_refs", [])),
            run_dir=resolved.run_dir,
        ) if taxonomy_request.get("evidence_refs") else []
        taxonomy_records.append(
            {
                "action_id": action_id,
                "paper_id": resolved.paper_id,
                "run_id": resolved.run_id,
                "taxonomy_version": taxonomy_version,
                "proposed_path": taxonomy_request["proposed_path"],
                "reason": taxonomy_request["reason"],
                **(
                    {"evidence_refs": request_evidence_refs}
                    if request_evidence_refs
                    else {}
                ),
            }
        )
        taxonomy_request_ref = relative_ref(
            taxonomy_requests_path,
            resolved.run_dir,
        )
    adjudication_records: list[dict[str, Any]] = []
    if unresolved:
        adjudication_records.append(
            {
                "action_id": action_id,
                "paper_id": resolved.paper_id,
                "run_id": resolved.run_id,
                "taxonomy_version": taxonomy_version,
                "primary_path": primary_path,
                "review_reasons": review_reasons,
                "status": status.value,
            }
        )
    action = ClassificationActionRecord(
        action_id=action_id,
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        taxonomy_version=taxonomy_version,
        mode=mode,
        outcome=outcome,
        status=status,
        summary=str(evidence["summary"]),
        prior_decision_ref=relative_ref(prior_path, resolved.run_dir),
        final_decision_ref=relative_ref(final_decision_path, resolved.run_dir),
        writeback_preview_ref=relative_ref(
            action_preview_path,
            resolved.run_dir,
        ),
        evidence_refs=evidence_refs,
        taxonomy_change_request_ref=taxonomy_request_ref,
    )
    plan_path = resolved.run_dir / CLASSIFICATION_PLAN_REF
    bundled_default = default_profile or DEFAULT_MODEL_PROFILE_BUNDLE["default_profile"]
    plan = ClassificationPlanRecord(
        run_id=resolved.run_id,
        taxonomy_version=taxonomy_version,
        mode=mode,
        papers=[
            {
                "paper_id": resolved.paper_id,
                "decision_ref": relative_ref(final_decision_path, resolved.run_dir),
                "decision_markdown_ref": relative_ref(
                    final_markdown_path,
                    resolved.run_dir,
                ),
                "status": status.value,
                "writeback_preview_ref": relative_ref(
                    canonical_preview_path,
                    resolved.run_dir,
                ),
                "action_ref": relative_ref(action_record_path, resolved.run_dir),
            }
        ],
        default_profile=bundled_default,
    )
    return _PreparedClassificationAction(
        resolved=resolved,
        action=action,
        final_decision=final_decision,
        plan=plan,
        preview_payload=preview_payload,
        taxonomy_records=taxonomy_records,
        adjudication_records=adjudication_records,
        action_record_path=action_record_path,
        action_markdown_path=action_markdown_path,
        final_decision_path=final_decision_path,
        final_markdown_path=final_markdown_path,
        action_preview_path=action_preview_path,
        canonical_preview_path=canonical_preview_path,
        taxonomy_requests_path=taxonomy_requests_path,
        adjudication_queue_path=adjudication_queue_path,
        plan_path=plan_path,
    )


def _prepare_classification_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None,
    item_key: str | None,
    default_profile: str | None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> _PreparedClassification:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    _require_passing_acceptance(resolved)

    evidence = _load_classification_evidence(evidence_path)
    taxonomy_version = str(evidence["taxonomy_version"])
    mode = ClassificationMode(str(evidence.get("mode", "single")))
    rejected_alternatives = [
        RejectedAlternativeRecord.from_dict(record)
        for record in evidence.get("rejected_alternatives", [])
    ]
    review_reasons = list(evidence.get("review_reasons", []))
    qa_flags = list(evidence.get("qa_flags", []))
    status = (
        ClassificationStatus.ADJUDICATION_REQUIRED
        if evidence.get("adjudication_required")
        else ClassificationStatus.NEEDS_REVIEW
        if review_reasons
        else ClassificationStatus.CLASSIFIED
    )

    preview_payload = _build_writeback_preview(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        evidence=evidence,
    )
    preview_path = resolved.run_dir / WRITEBACK_PREVIEW_REF
    evidence_refs = _normalize_evidence_refs(
        evidence_refs=list(evidence.get("evidence_refs", [])),
        run_dir=resolved.run_dir,
    )

    decision_dir = resolved.run_dir / DECISION_DIR_REF
    decision_json_path = decision_dir / f"{resolved.paper_id}.json"
    decision_markdown_path = decision_dir / f"{resolved.paper_id}.md"
    decision = ClassificationDecisionRecord(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        taxonomy_version=taxonomy_version,
        mode=mode,
        status=status,
        primary_path=str(evidence["primary_path"]),
        confidence=str(evidence["confidence"]),
        evidence_refs=evidence_refs,
        strongest_rejected_path=evidence.get("strongest_rejected_path"),
        rejected_alternatives=rejected_alternatives,
        review_reasons=review_reasons,
        qa_flags=qa_flags,
        writeback_preview_ref=relative_ref(preview_path, resolved.run_dir),
    )
    rejected_path = resolved.run_dir / REJECTED_ALTERNATIVES_REF
    adjudication_queue_path = resolved.run_dir / ADJUDICATION_QUEUE_REF
    taxonomy_requests_path = resolved.run_dir / TAXONOMY_CHANGE_REQUESTS_REF
    taxonomy_request = evidence.get("taxonomy_change_request")
    taxonomy_records: list[dict[str, Any]] = []
    if isinstance(taxonomy_request, dict):
        taxonomy_records.append(dict(taxonomy_request))
    plan_path = resolved.run_dir / CLASSIFICATION_PLAN_REF
    bundled_default = default_profile or DEFAULT_MODEL_PROFILE_BUNDLE["default_profile"]
    plan = ClassificationPlanRecord(
        run_id=resolved.run_id,
        taxonomy_version=taxonomy_version,
        mode=mode,
        papers=[
            {
                "paper_id": resolved.paper_id,
                "decision_ref": relative_ref(decision_json_path, resolved.run_dir),
                "decision_markdown_ref": relative_ref(
                    decision_markdown_path,
                    resolved.run_dir,
                ),
                "status": decision.status.value,
                "writeback_preview_ref": relative_ref(preview_path, resolved.run_dir),
            }
        ],
        default_profile=bundled_default,
    )
    return _PreparedClassification(
        resolved=resolved,
        decision=decision,
        plan=plan,
        preview_payload=preview_payload,
        taxonomy_records=taxonomy_records,
        plan_path=plan_path,
        decision_json_path=decision_json_path,
        decision_markdown_path=decision_markdown_path,
        rejected_path=rejected_path,
        adjudication_queue_path=adjudication_queue_path,
        taxonomy_requests_path=taxonomy_requests_path,
        preview_path=preview_path,
        batch_report_path=resolved.run_dir / BATCH_REPORT_REF,
    )


def _require_passing_acceptance(resolved: ResolvedRunArtifacts) -> None:
    acceptance_summary = load_json_object(
        resolved.run_dir / ACCEPTANCE_SUMMARY_REF,
        "acceptance summary",
    )
    if acceptance_summary.get("status") != "pass":
        raise MillefeuilleContractError(
            "classification preview requires an acceptance summary with status pass"
        )
    expected_acceptance_identity = {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "source_hash": resolved.source_hash,
    }
    for field_name, expected in expected_acceptance_identity.items():
        actual = acceptance_summary.get(field_name)
        if actual != expected:
            raise MillefeuilleContractError(
                f"acceptance summary {field_name} drift: "
                f"expected {expected!r}, got {actual!r}"
            )


def _persist_classification(
    prepared: _PreparedClassification,
) -> ClassificationWriteResult:
    resolved = prepared.resolved
    decision = prepared.decision
    plan = prepared.plan
    write_json_object(prepared.preview_path, prepared.preview_payload)
    write_json_object(prepared.decision_json_path, decision.to_dict())
    write_text(
        prepared.decision_markdown_path,
        _render_decision_markdown(decision),
    )
    write_jsonl_records(
        prepared.rejected_path,
        [alt.to_dict() for alt in decision.rejected_alternatives],
    )
    adjudication_records: list[dict[str, Any]] = []
    if decision.status == ClassificationStatus.ADJUDICATION_REQUIRED:
        adjudication_records.append(
            {
                "paper_id": resolved.paper_id,
                "run_id": resolved.run_id,
                "taxonomy_version": decision.taxonomy_version,
                "primary_path": decision.primary_path,
                "review_reasons": decision.review_reasons
                or ["adjudication requested"],
            }
        )
    write_jsonl_records(prepared.adjudication_queue_path, adjudication_records)
    write_jsonl_records(
        prepared.taxonomy_requests_path,
        prepared.taxonomy_records,
    )
    write_json_object(prepared.plan_path, plan.to_dict())
    write_text(prepared.batch_report_path, _render_batch_report(decision, plan))

    stage_status = (
        StageStatus.PASSED.value
        if decision.status == ClassificationStatus.CLASSIFIED
        else StageStatus.NEEDS_REVIEW.value
    )
    stage_manifest = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.CLASSIFY,
        status=stage_status,
        outputs=[
            relative_ref(prepared.plan_path, resolved.run_dir),
            relative_ref(prepared.decision_json_path, resolved.run_dir),
            relative_ref(prepared.preview_path, resolved.run_dir),
        ],
        notes=[f"classification preview written for {resolved.paper_id}"],
    )

    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="classification_plan",
        kind="classification-plan",
        ref=relative_ref(prepared.plan_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision",
        kind="classification-decision",
        ref=relative_ref(prepared.decision_json_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision_markdown",
        kind="classification-decision-markdown",
        ref=relative_ref(prepared.decision_markdown_path, resolved.run_dir),
        format="markdown",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="zotero_writeback_preview",
        kind="zotero-writeback-preview",
        ref=relative_ref(prepared.preview_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = update_artifact_index(
        artifact_index,
        stage_name=StageName.CLASSIFY,
        stage_status=stage_status,
        zotero_writeback={
            "mode": "preview",
            "status": "previewed",
            "plan_ref": relative_ref(prepared.preview_path, resolved.run_dir),
        },
    )
    persist_run_artifacts(
        resolved,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
    return ClassificationWriteResult(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        status=decision.status.value,
        plan_path=prepared.plan_path,
        decision_json_path=prepared.decision_json_path,
        writeback_preview_path=prepared.preview_path,
    )


def _preflight_existing_action(prepared: _PreparedClassificationAction) -> None:
    action_dir = prepared.action_record_path.parent
    if not action_dir.exists():
        return
    expected_files = {
        prepared.action_record_path,
        prepared.action_markdown_path,
        prepared.final_decision_path,
        prepared.final_markdown_path,
        prepared.action_preview_path,
        prepared.taxonomy_requests_path,
        prepared.adjudication_queue_path,
    }
    existing_files = {path for path in action_dir.rglob("*") if path.is_file()}
    if existing_files != expected_files:
        raise MillefeuilleContractError(
            f"classification action {prepared.action.action_id!r} already "
            "exists with incomplete or unexpected artifacts"
        )
    expected_json = {
        prepared.action_record_path: prepared.action.to_dict(),
        prepared.final_decision_path: prepared.final_decision.to_dict(),
        prepared.action_preview_path: prepared.preview_payload,
    }
    for path, expected in expected_json.items():
        if load_json_object(path, "existing classification action artifact") != (
            expected
        ):
            raise MillefeuilleContractError(
                f"classification action {prepared.action.action_id!r} already "
                f"exists with drift at {path.name}"
            )
    expected_text = {
        prepared.action_markdown_path: _render_classification_action_markdown(
            prepared.action
        ),
        prepared.final_markdown_path: _render_decision_markdown(
            prepared.final_decision
        ),
    }
    for path, expected in expected_text.items():
        if path.read_text(encoding="utf-8") != expected:
            raise MillefeuilleContractError(
                f"classification action {prepared.action.action_id!r} already "
                f"exists with drift at {path.name}"
            )
    expected_jsonl = {
        prepared.taxonomy_requests_path: prepared.taxonomy_records,
        prepared.adjudication_queue_path: prepared.adjudication_records,
    }
    for path, expected in expected_jsonl.items():
        if load_jsonl_records(
            path,
            "existing classification action JSONL",
        ) != expected:
            raise MillefeuilleContractError(
                f"classification action {prepared.action.action_id!r} already "
                f"exists with drift at {path.name}"
            )


def _persist_classification_action(
    prepared: _PreparedClassificationAction,
) -> ClassificationActionWriteResult:
    resolved = prepared.resolved
    action = prepared.action
    decision = prepared.final_decision
    write_json_object(prepared.action_preview_path, prepared.preview_payload)
    write_json_object(prepared.canonical_preview_path, prepared.preview_payload)
    write_json_object(prepared.final_decision_path, decision.to_dict())
    write_text(
        prepared.final_markdown_path,
        _render_decision_markdown(decision),
    )
    write_jsonl_records(
        prepared.taxonomy_requests_path,
        prepared.taxonomy_records,
    )
    write_jsonl_records(
        prepared.adjudication_queue_path,
        prepared.adjudication_records,
    )
    write_json_object(prepared.action_record_path, action.to_dict())
    write_text(
        prepared.action_markdown_path,
        _render_classification_action_markdown(action),
    )
    write_json_object(prepared.plan_path, prepared.plan.to_dict())

    stage_status = (
        StageStatus.PASSED.value
        if decision.status == ClassificationStatus.CLASSIFIED
        else StageStatus.NEEDS_REVIEW.value
    )
    stage_manifest = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.CLASSIFY,
        status=stage_status,
        inputs=[action.prior_decision_ref, *action.evidence_refs],
        outputs=[
            relative_ref(prepared.plan_path, resolved.run_dir),
            action.final_decision_ref,
            relative_ref(prepared.action_record_path, resolved.run_dir),
            action.writeback_preview_ref,
            relative_ref(prepared.canonical_preview_path, resolved.run_dir),
        ],
        notes=[
            f"classification {action.mode.value} action "
            f"{action.action_id} recorded as {action.outcome.value}"
        ],
    )
    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="classification_action",
        kind="classification-action",
        ref=relative_ref(prepared.action_record_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision",
        kind="classification-decision",
        ref=action.final_decision_ref,
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision_markdown",
        kind="classification-decision-markdown",
        ref=relative_ref(prepared.final_markdown_path, resolved.run_dir),
        format="markdown",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="zotero_writeback_preview",
        kind="zotero-writeback-preview",
        ref=relative_ref(prepared.canonical_preview_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = update_artifact_index(
        artifact_index,
        stage_name=StageName.CLASSIFY,
        stage_status=stage_status,
        zotero_writeback={
            "mode": "preview",
            "status": "previewed",
            "plan_ref": relative_ref(
                prepared.canonical_preview_path,
                resolved.run_dir,
            ),
        },
    )
    persist_run_artifacts(
        resolved,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
    return ClassificationActionWriteResult(
        action_id=action.action_id,
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        mode=action.mode.value,
        outcome=action.outcome.value,
        status=action.status.value,
        action_record_path=prepared.action_record_path,
        final_decision_path=prepared.final_decision_path,
        writeback_preview_path=prepared.canonical_preview_path,
    )


def _load_classification_batch_manifest(
    path: str | Path,
) -> tuple[str, str, list[dict[str, str]]]:
    payload = load_json_object(path, "classification batch manifest")
    allowed_fields = {"schema_version", "batch_id", "taxonomy_version", "runs"}
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise MillefeuilleContractError(
            "classification batch manifest has unsupported fields: "
            + ", ".join(unexpected)
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise MillefeuilleContractError(
            "classification batch schema_version must be a string"
        )
    if schema_version != CLASSIFICATION_BATCH_MANIFEST_SCHEMA:
        raise MillefeuilleContractError(
            f"unsupported classification batch schema_version {schema_version!r}"
        )
    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str):
        raise MillefeuilleContractError(
            "classification batch batch_id must be a string"
        )
    if batch_id in {".", ".."} or _SAFE_BATCH_ID.fullmatch(batch_id) is None:
        raise MillefeuilleContractError(
            "batch_id must be traversal-safe and use only letters, numbers, "
            "'.', '_', or '-'"
        )
    taxonomy_version = payload.get("taxonomy_version")
    if not isinstance(taxonomy_version, str) or not taxonomy_version.strip():
        raise MillefeuilleContractError(
            "classification batch taxonomy_version must be a non-empty string"
        )
    taxonomy_version = taxonomy_version.strip()
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise MillefeuilleContractError(
            "classification batch runs must be a non-empty array"
        )

    locators: list[dict[str, str]] = []
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict):
            raise MillefeuilleContractError(
                f"classification batch run {index} must be an object"
            )
        unexpected = sorted(
            set(entry) - {"paper_id", "item_key", "run_id", "evidence_ref"}
        )
        if unexpected:
            raise MillefeuilleContractError(
                f"classification batch run {index} has unsupported fields: "
                + ", ".join(unexpected)
            )
        required = [
            field_name
            for field_name in ("run_id", "evidence_ref")
            if field_name not in entry
        ]
        if required:
            raise MillefeuilleContractError(
                f"classification batch run {index} requires "
                + ", ".join(required)
            )
        has_paper_id = "paper_id" in entry
        has_item_key = "item_key" in entry
        if has_paper_id == has_item_key:
            raise MillefeuilleContractError(
                f"classification batch run {index} requires exactly one of "
                "paper_id or item_key"
            )
        locator_field = "paper_id" if has_paper_id else "item_key"
        typed_fields = {
            "run_id": entry["run_id"],
            locator_field: entry[locator_field],
            "evidence_ref": entry["evidence_ref"],
        }
        non_string_fields = [
            field_name
            for field_name, value in typed_fields.items()
            if not isinstance(value, str)
        ]
        if non_string_fields:
            raise MillefeuilleContractError(
                f"classification batch run {index} has non-string fields: "
                + ", ".join(non_string_fields)
            )
        unsafe_fields = [
            field_name
            for field_name in ("run_id", locator_field)
            if _SAFE_BATCH_LOCATOR.fullmatch(typed_fields[field_name]) is None
        ]
        if unsafe_fields:
            raise MillefeuilleContractError(
                f"classification batch run {index} has unsafe locator fields: "
                + ", ".join(unsafe_fields)
            )
        evidence_ref = _normalize_batch_evidence_ref(
            typed_fields["evidence_ref"],
            index=index,
        )
        locators.append(
            {
                "run_id": typed_fields["run_id"],
                locator_field: typed_fields[locator_field],
                "evidence_ref": evidence_ref,
            }
        )
    return batch_id, taxonomy_version, locators


def _normalize_batch_evidence_ref(value: str, *, index: int) -> str:
    if "\\" in value:
        raise MillefeuilleContractError(
            f"classification batch run {index} evidence_ref must use '/'"
        )
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(_SAFE_EVIDENCE_REF_PART.fullmatch(part) is None for part in path.parts)
    ):
        raise MillefeuilleContractError(
            f"classification batch run {index} evidence_ref must be a "
            "traversal-safe relative path"
        )
    return path.as_posix()


def _build_classification_batch_routes(
    runs: list[ClassificationBatchRunRecord],
) -> list[dict[str, Any]]:
    routed: dict[str, list[dict[str, str]]] = {}
    for run in runs:
        routed.setdefault(run.primary_path, []).append(
            {
                "paper_id": run.paper_id,
                "run_id": run.run_id,
                "status": run.status.value,
                "decision_ref": run.decision_ref,
            }
        )
    routes: list[dict[str, Any]] = []
    for primary_path in sorted(routed):
        route_runs = sorted(
            routed[primary_path],
            key=lambda run: (run["paper_id"], run["run_id"]),
        )
        routes.append(
            {
                "primary_path": primary_path,
                "count": len(route_runs),
                "runs": route_runs,
            }
        )
    return routes


def _load_classification_evidence(path: str | Path) -> dict[str, Any]:
    payload = load_json_object(path, "classification evidence")
    schema_version = str(payload.get("schema_version", "")).strip()
    if schema_version != CLASSIFICATION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            f"unsupported classification evidence schema_version {schema_version!r}"
        )
    required_strings = {
        "taxonomy_version": payload.get("taxonomy_version"),
        "primary_path": payload.get("primary_path"),
        "confidence": payload.get("confidence"),
    }
    for field_name, value in required_strings.items():
        if not isinstance(value, str) or not value.strip():
            raise MillefeuilleContractError(
                f"classification evidence {field_name} must be non-empty"
            )
    evidence_refs = payload.get("evidence_refs", [])
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise MillefeuilleContractError(
            "classification evidence evidence_refs must be a non-empty array"
        )
    rejected = payload.get("rejected_alternatives", [])
    if not isinstance(rejected, list):
        raise MillefeuilleContractError(
            "classification evidence rejected_alternatives must be an array"
        )
    mode = payload.get("mode", ClassificationMode.SINGLE.value)
    if not isinstance(mode, str):
        raise MillefeuilleContractError(
            "classification evidence mode must be a string"
        )
    try:
        ClassificationMode(mode)
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"unsupported classification evidence mode {mode!r}"
        ) from exc
    for field_name in ("review_reasons", "qa_flags"):
        value = payload.get(field_name, [])
        if not isinstance(value, list):
            raise MillefeuilleContractError(
                f"classification evidence {field_name} must be an array"
            )
    if "adjudication_required" in payload and not isinstance(
        payload["adjudication_required"], bool
    ):
        raise MillefeuilleContractError(
            "classification evidence adjudication_required must be a boolean"
        )
    for field_name in ("taxonomy_change_request", "writeback_preview"):
        value = payload.get(field_name)
        if value is not None and not isinstance(value, dict):
            raise MillefeuilleContractError(
                f"classification evidence {field_name} must be an object"
            )
    return payload


def _load_classification_action_evidence(path: str | Path) -> dict[str, Any]:
    payload = load_json_object(path, "classification action evidence")
    allowed_fields = {
        "schema_version",
        "action_id",
        "mode",
        "outcome",
        "taxonomy_version",
        "prior_decision_ref",
        "primary_path",
        "confidence",
        "summary",
        "evidence_refs",
        "strongest_rejected_path",
        "rejected_alternatives",
        "qa_flags",
        "review_reasons",
        "taxonomy_change_request",
        "writeback_preview",
    }
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise MillefeuilleContractError(
            "classification action evidence has unsupported fields: "
            + ", ".join(unexpected)
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise MillefeuilleContractError(
            "classification action evidence schema_version must be a string"
        )
    if schema_version != CLASSIFICATION_ACTION_EVIDENCE_SCHEMA_VERSION:
        raise MillefeuilleContractError(
            "unsupported classification action evidence schema_version "
            f"{schema_version!r}"
        )
    required_strings = (
        "action_id",
        "mode",
        "outcome",
        "taxonomy_version",
        "prior_decision_ref",
        "primary_path",
        "confidence",
        "summary",
    )
    for field_name in required_strings:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise MillefeuilleContractError(
                f"classification action evidence {field_name} must be a "
                "non-empty string"
            )
    action_id = payload["action_id"]
    if action_id in {".", ".."} or _SAFE_BATCH_ID.fullmatch(action_id) is None:
        raise MillefeuilleContractError(
            "classification action_id must be traversal-safe and use only "
            "letters, numbers, '.', '_', or '-'"
        )
    try:
        mode = ClassificationMode(payload["mode"])
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"unsupported classification action mode {payload['mode']!r}"
        ) from exc
    if mode not in {ClassificationMode.REVIEW, ClassificationMode.ADJUDICATE}:
        raise MillefeuilleContractError(
            "classification action mode must be review or adjudicate"
        )
    try:
        outcome = ClassificationActionOutcome(payload["outcome"])
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"unsupported classification action outcome {payload['outcome']!r}"
        ) from exc
    allowed_outcomes = {
        ClassificationMode.REVIEW: {
            ClassificationActionOutcome.NO_CHANGE,
            ClassificationActionOutcome.CORRECTED,
            ClassificationActionOutcome.ESCALATED,
        },
        ClassificationMode.ADJUDICATE: {
            ClassificationActionOutcome.CONFIRMED,
            ClassificationActionOutcome.CORRECTED,
            ClassificationActionOutcome.TAXONOMY_CHANGE_REQUESTED,
        },
    }
    if outcome not in allowed_outcomes[mode]:
        raise MillefeuilleContractError(
            f"outcome {outcome.value!r} is invalid for mode {mode.value!r}"
        )
    for field_name in ("evidence_refs", "qa_flags", "review_reasons"):
        value = payload.get(field_name, [])
        if not isinstance(value, list):
            raise MillefeuilleContractError(
                f"classification action evidence {field_name} must be an array"
            )
        for entry in value:
            if not isinstance(entry, str) or not entry.strip():
                raise MillefeuilleContractError(
                    f"classification action evidence {field_name} must contain "
                    "non-empty strings"
                )
    if not payload.get("evidence_refs"):
        raise MillefeuilleContractError(
            "classification action evidence evidence_refs must be non-empty"
        )
    strongest = payload.get("strongest_rejected_path")
    if strongest is not None and (
        not isinstance(strongest, str) or not strongest.strip()
    ):
        raise MillefeuilleContractError(
            "classification action evidence strongest_rejected_path must be "
            "a non-empty string when provided"
        )
    rejected = payload.get("rejected_alternatives", [])
    if not isinstance(rejected, list):
        raise MillefeuilleContractError(
            "classification action evidence rejected_alternatives must be an array"
        )
    for index, record in enumerate(rejected):
        if not isinstance(record, dict):
            raise MillefeuilleContractError(
                f"classification action rejected alternative {index} must be "
                "an object"
            )
        extra = sorted(set(record) - {"path", "reason", "evidence_refs"})
        if extra:
            raise MillefeuilleContractError(
                f"classification action rejected alternative {index} has "
                "unsupported fields: " + ", ".join(extra)
            )
        for field_name in ("path", "reason"):
            value = record.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    f"classification action rejected alternative {index} "
                    f"{field_name} must be a non-empty string"
                )
        alternative_refs = record.get("evidence_refs", [])
        if not isinstance(alternative_refs, list):
            raise MillefeuilleContractError(
                f"classification action rejected alternative {index} "
                "evidence_refs must be an array"
            )
        for ref in alternative_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    f"classification action rejected alternative {index} "
                    "evidence_refs must contain non-empty strings"
                )
        RejectedAlternativeRecord.from_dict(record)
    taxonomy_request = payload.get("taxonomy_change_request")
    if taxonomy_request is not None:
        if not isinstance(taxonomy_request, dict):
            raise MillefeuilleContractError(
                "classification action taxonomy_change_request must be an object"
            )
        extra = sorted(
            set(taxonomy_request) - {"proposed_path", "reason", "evidence_refs"}
        )
        if extra:
            raise MillefeuilleContractError(
                "classification action taxonomy_change_request has unsupported "
                "fields: " + ", ".join(extra)
            )
        for field_name in ("proposed_path", "reason"):
            value = taxonomy_request.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise MillefeuilleContractError(
                    "classification action taxonomy_change_request "
                    f"{field_name} must be a non-empty string"
                )
        request_refs = taxonomy_request.get("evidence_refs", [])
        if not isinstance(request_refs, list):
            raise MillefeuilleContractError(
                "classification action taxonomy_change_request evidence_refs "
                "must be an array"
            )
        for ref in request_refs:
            if not isinstance(ref, str) or not ref.strip():
                raise MillefeuilleContractError(
                    "classification action taxonomy_change_request evidence_refs "
                    "must contain non-empty strings"
                )
    preview = payload.get("writeback_preview")
    if preview is not None:
        if not isinstance(preview, dict):
            raise MillefeuilleContractError(
                "classification action writeback_preview must be an object"
            )
        extra = sorted(
            set(preview)
            - {"add_tags", "remove_tags", "destination_collection", "note_markdown"}
        )
        if extra:
            raise MillefeuilleContractError(
                "classification action writeback_preview has unsupported fields: "
                + ", ".join(extra)
            )
        for field_name in ("add_tags", "remove_tags"):
            values = preview.get(field_name, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise MillefeuilleContractError(
                    "classification action writeback_preview "
                    f"{field_name} must be an array of non-empty strings"
                )
        for field_name in ("destination_collection", "note_markdown"):
            value = preview.get(field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise MillefeuilleContractError(
                    "classification action writeback_preview "
                    f"{field_name} must be a non-empty string when provided"
                )
    return payload


def _resolve_safe_run_file_ref(*, ref: str, run_dir: Path, label: str) -> Path:
    if "\\" in ref:
        raise MillefeuilleContractError(f"{label} must use '/' path separators")
    relative = Path(ref)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(
            _SAFE_EVIDENCE_REF_PART.fullmatch(part) is None
            for part in relative.parts
        )
    ):
        raise MillefeuilleContractError(
            f"{label} must be a traversal-safe relative path under the run"
        )
    target = run_dir / relative
    try:
        target.resolve().relative_to(run_dir.resolve())
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"{label} resolves outside the run directory"
        ) from exc
    if not target.is_file():
        raise MillefeuilleContractError(f"{label} does not exist: {ref}")
    return target


def _normalize_action_evidence_refs(
    *,
    evidence_refs: list[str],
    run_dir: Path,
) -> list[str]:
    return [
        relative_ref(
            _resolve_safe_run_file_ref(
                ref=ref,
                run_dir=run_dir,
                label="classification action evidence_ref",
            ),
            run_dir,
        )
        for ref in evidence_refs
    ]


def _normalize_evidence_refs(
    *,
    evidence_refs: list[str],
    run_dir: Path,
) -> list[str]:
    normalized: list[str] = []
    for ref in evidence_refs:
        if not isinstance(ref, str) or not ref.strip():
            raise MillefeuilleContractError(
                "classification evidence_refs must contain non-empty strings"
            )
        path = Path(ref)
        normalized.append(path.as_posix() if path.is_absolute() else ref)
        if not path.is_absolute() and not (run_dir / ref).exists():
            raise MillefeuilleContractError(
                f"classification evidence ref does not exist under run dir: {ref}"
            )
    return normalized


def _build_writeback_preview(
    *,
    paper_id: str,
    run_id: str,
    source_hash: str,
    evidence: dict[str, Any],
    classified: bool = True,
) -> dict[str, Any]:
    preview = dict(evidence.get("writeback_preview") or {})
    add_tags = list(preview.get("add_tags") or [])
    remove_tags = list(preview.get("remove_tags") or [])
    if not add_tags:
        add_tags = (
            [
                "millefeuille-acceptance-passed",
                "millefeuille-ready-for-classification",
                "millefeuille-classified",
            ]
            if classified
            else ["millefeuille-classification-review"]
        )
    if not remove_tags:
        remove_tags = ["millefeuille"]
    payload: dict[str, Any] = {
        "schema_version": "millefeuille-zotero-writeback-preview/v0.1",
        "paper_id": paper_id,
        "run_id": run_id,
        "source_hash": source_hash,
        "mode": "preview",
        "status": "previewed",
        "add_tags": add_tags,
        "remove_tags": remove_tags,
        "destination_collection": preview.get("destination_collection")
        or evidence.get("primary_path"),
        "classification_path": evidence.get("primary_path"),
    }
    note_markdown = preview.get("note_markdown")
    if isinstance(note_markdown, str) and note_markdown.strip():
        payload["note_markdown"] = note_markdown.strip()
    return payload


def _render_decision_markdown(decision: ClassificationDecisionRecord) -> str:
    lines = [
        f"# Classification Decision: {decision.paper_id}",
        "",
        f"- Run: `{decision.run_id}`",
        f"- Taxonomy version: `{decision.taxonomy_version}`",
        f"- Mode: `{decision.mode.value}`",
        f"- Status: `{decision.status.value}`",
        f"- Primary path: `{decision.primary_path}`",
        f"- Confidence: `{decision.confidence}`",
        "",
        "## Evidence Refs",
    ]
    for ref in decision.evidence_refs:
        lines.append(f"- `{ref}`")
    if decision.rejected_alternatives:
        lines.extend(["", "## Rejected Alternatives"])
        for alt in decision.rejected_alternatives:
            lines.append(f"- `{alt.path}`: {alt.reason}")
    if decision.review_reasons:
        lines.extend(["", "## Review Reasons"])
        for reason in decision.review_reasons:
            lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def _render_classification_action_markdown(
    action: ClassificationActionRecord,
) -> str:
    lines = [
        f"# Classification Action: {action.action_id}",
        "",
        f"- Paper: `{action.paper_id}`",
        f"- Run: `{action.run_id}`",
        f"- Taxonomy version: `{action.taxonomy_version}`",
        f"- Mode: `{action.mode.value}`",
        f"- Outcome: `{action.outcome.value}`",
        f"- Status: `{action.status.value}`",
        f"- Prior decision: `{action.prior_decision_ref}`",
        f"- Final decision: `{action.final_decision_ref}`",
        f"- Writeback preview: `{action.writeback_preview_ref}`",
        "",
        "## Summary",
        "",
        action.summary,
        "",
        "## Evidence Refs",
    ]
    for ref in action.evidence_refs:
        lines.append(f"- `{ref}`")
    if action.taxonomy_change_request_ref is not None:
        lines.extend(
            [
                "",
                "## Taxonomy Change Request",
                "",
                f"- `{action.taxonomy_change_request_ref}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_batch_report(
    decision: ClassificationDecisionRecord,
    plan: ClassificationPlanRecord,
) -> str:
    lines = [
        "# Batch Classification Report",
        "",
        f"- Run: `{plan.run_id}`",
        f"- Mode: `{plan.mode.value}`",
        f"- Taxonomy version: `{plan.taxonomy_version}`",
        f"- Papers: `{len(plan.papers)}`",
        f"- Decision status: `{decision.status.value}`",
        f"- Primary path: `{decision.primary_path}`",
    ]
    return "\n".join(lines) + "\n"


def _render_classification_batch_markdown(
    summary: ClassificationBatchSummaryRecord,
) -> str:
    lines = [
        "# Batch Classification Report",
        "",
        f"- Batch: `{summary.batch_id}`",
        f"- Taxonomy version: `{summary.taxonomy_version}`",
        f"- Status: `{summary.status.value}`",
        f"- Runs: `{summary.counts['runs']}`",
        f"- Classified: `{summary.counts['classified']}`",
        f"- Needs review: `{summary.counts['needs_review']}`",
        f"- Adjudication required: `{summary.counts['adjudication_required']}`",
        "",
        "## Routes",
    ]
    for route in summary.routes:
        lines.append(
            f"- `{route['primary_path']}`: `{route['count']}` run(s)"
        )
        for run in route["runs"]:
            lines.append(
                f"  - `{run['paper_id']}` / `{run['run_id']}`: "
                f"`{run['status']}` (`{run['decision_ref']}`)"
            )
    review_runs = [
        run
        for run in summary.runs
        if run.status != ClassificationStatus.CLASSIFIED
    ]
    if review_runs:
        lines.extend(["", "## Review And Adjudication Queue"])
        for run in review_runs:
            lines.append(
                f"- `{run.paper_id}` / `{run.run_id}`: `{run.status.value}`"
            )
            for reason in run.review_reasons:
                lines.append(f"  review: {reason}")
    return "\n".join(lines) + "\n"
