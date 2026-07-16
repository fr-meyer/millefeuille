"""Offline classification orchestration for accepted Millefeuille runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.millefeuille import (
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
    load_json_object,
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


def write_classification_from_evidence(
    *,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    default_profile: str | None = None,
) -> ClassificationWriteResult:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
    )
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
    write_json_object(preview_path, preview_payload)
    write_json_object(decision_json_path, decision.to_dict())
    write_text(decision_markdown_path, _render_decision_markdown(decision))

    rejected_path = resolved.run_dir / REJECTED_ALTERNATIVES_REF
    write_jsonl_records(
        rejected_path,
        [alt.to_dict() for alt in rejected_alternatives],
    )

    adjudication_queue_path = resolved.run_dir / ADJUDICATION_QUEUE_REF
    adjudication_records: list[dict[str, Any]] = []
    if status == ClassificationStatus.ADJUDICATION_REQUIRED:
        adjudication_records.append(
            {
                "paper_id": resolved.paper_id,
                "run_id": resolved.run_id,
                "taxonomy_version": taxonomy_version,
                "primary_path": evidence["primary_path"],
                "review_reasons": review_reasons or ["adjudication requested"],
            }
        )
    write_jsonl_records(adjudication_queue_path, adjudication_records)

    taxonomy_requests_path = resolved.run_dir / TAXONOMY_CHANGE_REQUESTS_REF
    taxonomy_request = evidence.get("taxonomy_change_request")
    taxonomy_records: list[dict[str, Any]] = []
    if isinstance(taxonomy_request, dict):
        taxonomy_records.append(dict(taxonomy_request))
    write_jsonl_records(taxonomy_requests_path, taxonomy_records)

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
    write_json_object(plan_path, plan.to_dict())

    batch_report_path = resolved.run_dir / BATCH_REPORT_REF
    write_text(batch_report_path, _render_batch_report(decision, plan))

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
            relative_ref(plan_path, resolved.run_dir),
            relative_ref(decision_json_path, resolved.run_dir),
            relative_ref(preview_path, resolved.run_dir),
        ],
        notes=[f"classification preview written for {resolved.paper_id}"],
    )

    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="classification_plan",
        kind="classification-plan",
        ref=relative_ref(plan_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision",
        kind="classification-decision",
        ref=relative_ref(decision_json_path, resolved.run_dir),
        format="json",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="classification_decision_markdown",
        kind="classification-decision-markdown",
        ref=relative_ref(decision_markdown_path, resolved.run_dir),
        format="markdown",
        stage=StageName.CLASSIFY.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="zotero_writeback_preview",
        kind="zotero-writeback-preview",
        ref=relative_ref(preview_path, resolved.run_dir),
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
            "plan_ref": relative_ref(preview_path, resolved.run_dir),
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
        plan_path=plan_path,
        decision_json_path=decision_json_path,
        writeback_preview_path=preview_path,
    )


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
    return payload


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
) -> dict[str, Any]:
    preview = dict(evidence.get("writeback_preview") or {})
    add_tags = list(preview.get("add_tags") or [])
    remove_tags = list(preview.get("remove_tags") or [])
    if not add_tags:
        add_tags = [
            "millefeuille-acceptance-passed",
            "millefeuille-ready-for-classification",
            "millefeuille-classified",
        ]
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
