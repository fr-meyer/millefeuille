"""Offline acceptance synthesis for verified Millefeuille runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.card_fixtures import CARD_JSON_REF, load_paper_card
from millefeuille.domain.card_index_contract import (
    load_and_validate_canonical_card_index,
    validate_paper_card_identity,
)
from millefeuille.domain.extraction_fixtures import (
    NATIVE_EVIDENCE_REF,
    OCR_EVIDENCE_REF,
    load_native_extraction_sidecar,
    load_ocr_extraction_sidecar,
)
from millefeuille.domain.index_fixtures import (
    INDEX_STATUS_REF,
    load_retrieval_index_status,
)
from millefeuille.domain.millefeuille import (
    AcceptanceBatchRunRecord,
    AcceptanceBatchSummaryRecord,
    AcceptanceCheckRecord,
    AcceptanceCheckStatus,
    AcceptanceStatus,
    AcceptanceSummaryRecord,
    MillefeuilleContractError,
    StageName,
    StageStatus,
)
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    load_route_selection_sidecar,
)
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
    write_text,
)
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    load_structure_sidecar,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
)

ACCEPTANCE_SUMMARY_REF = Path("reports/acceptance-summary.json")
ACCEPTANCE_SUMMARY_MARKDOWN_REF = Path("reports/acceptance-summary.md")
ACCEPTANCE_BATCH_ROOT_REF = Path("batches/millefeuille")
ACCEPTANCE_BATCH_SUMMARY_REF = Path("reports/acceptance-batch-summary.json")
ACCEPTANCE_BATCH_SUMMARY_MARKDOWN_REF = Path("reports/acceptance-batch-summary.md")
ACCEPTANCE_BATCH_MANIFEST_SCHEMA = "millefeuille-acceptance-batch-manifest/v0.1"
_SAFE_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_BATCH_LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")


@dataclass(frozen=True)
class AcceptanceWriteResult:
    paper_id: str
    run_id: str
    status: str
    summary_path: Path
    markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "status": self.status,
            "summary_path": str(self.summary_path),
            "markdown_path": str(self.markdown_path),
        }


@dataclass(frozen=True)
class AcceptanceBatchWriteResult:
    batch_id: str
    status: str
    counts: dict[str, int]
    runs: list[dict[str, Any]]
    summary_path: Path
    markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "counts": dict(self.counts),
            "runs": [dict(run) for run in self.runs],
            "summary_path": str(self.summary_path),
            "markdown_path": str(self.markdown_path),
        }


def write_acceptance_summary(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    handoff_path: str | Path | None = None,
    duplicate_scan_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> AcceptanceWriteResult:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    summary = _build_acceptance_summary(
        resolved=resolved,
        handoff_path=handoff_path,
        duplicate_scan_path=duplicate_scan_path,
    )
    return _persist_acceptance_summary(resolved=resolved, summary=summary)


def write_acceptance_batch_summary(
    *,
    source_pack_root: str | Path,
    batch_manifest_path: str | Path,
    handoff_path: str | Path,
    duplicate_scan_path: str | Path | None = None,
) -> AcceptanceBatchWriteResult:
    """Synthesize acceptance for a validated set of existing offline runs.

    Every run is resolved and its acceptance summary is built before any output
    is persisted. Invalid manifests, duplicate run identities, missing run
    packages, or evidence drift therefore fail before batch-owned writes begin.
    """

    batch_id, run_locators = _load_acceptance_batch_manifest(batch_manifest_path)
    prepared: list[tuple[ResolvedRunArtifacts, AcceptanceSummaryRecord]] = []
    for locator in run_locators:
        resolved = resolve_run_artifacts(
            source_pack_root=source_pack_root,
            run_id=locator["run_id"],
            paper_id=locator.get("paper_id"),
            item_key=locator.get("item_key"),
        )
        summary = _build_acceptance_summary(
            resolved=resolved,
            handoff_path=handoff_path,
            duplicate_scan_path=duplicate_scan_path,
        )
        prepared.append((resolved, summary))

    prepared.sort(key=lambda item: (item[0].paper_id, item[0].run_id))
    identities = [(resolved.paper_id, resolved.run_id) for resolved, _ in prepared]
    if len(identities) != len(set(identities)):
        raise MillefeuilleContractError(
            "acceptance batch runs must have unique paper_id/run_id pairs"
        )

    root = Path(source_pack_root)
    run_records: list[AcceptanceBatchRunRecord] = []
    for resolved, summary in prepared:
        result = _persist_acceptance_summary(resolved=resolved, summary=summary)
        run_records.append(
            AcceptanceBatchRunRecord(
                paper_id=resolved.paper_id,
                run_id=resolved.run_id,
                source_hash=resolved.source_hash,
                status=summary.status,
                summary_ref=relative_ref(result.summary_path, root),
                review_reasons=list(summary.review_reasons),
            )
        )

    counts = {
        "runs": len(run_records),
        "passed": sum(run.status == AcceptanceStatus.PASS for run in run_records),
        "needs_review": sum(
            run.status == AcceptanceStatus.NEEDS_REVIEW for run in run_records
        ),
    }
    status = (
        AcceptanceStatus.PASS
        if counts["needs_review"] == 0
        else AcceptanceStatus.NEEDS_REVIEW
    )
    batch_summary = AcceptanceBatchSummaryRecord(
        batch_id=batch_id,
        status=status,
        counts=counts,
        runs=run_records,
    )
    batch_dir = root / ACCEPTANCE_BATCH_ROOT_REF / batch_id
    summary_path = batch_dir / ACCEPTANCE_BATCH_SUMMARY_REF
    markdown_path = batch_dir / ACCEPTANCE_BATCH_SUMMARY_MARKDOWN_REF
    write_json_object(summary_path, batch_summary.to_dict())
    write_text(markdown_path, _render_acceptance_batch_markdown(batch_summary))
    return AcceptanceBatchWriteResult(
        batch_id=batch_id,
        status=batch_summary.status.value,
        counts=dict(batch_summary.counts),
        runs=[run.to_dict() for run in batch_summary.runs],
        summary_path=summary_path,
        markdown_path=markdown_path,
    )


def _persist_acceptance_summary(
    *,
    resolved: ResolvedRunArtifacts,
    summary: AcceptanceSummaryRecord,
) -> AcceptanceWriteResult:
    summary_path = resolved.run_dir / ACCEPTANCE_SUMMARY_REF
    markdown_path = resolved.run_dir / ACCEPTANCE_SUMMARY_MARKDOWN_REF
    write_json_object(summary_path, summary.to_dict())
    write_text(markdown_path, _render_acceptance_markdown(summary))

    summary_ref = relative_ref(summary_path, resolved.run_dir)
    markdown_ref = relative_ref(markdown_path, resolved.run_dir)
    stage_status = (
        StageStatus.PASSED.value
        if summary.status == AcceptanceStatus.PASS
        else StageStatus.NEEDS_REVIEW.value
    )
    stage_manifest = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.ACCEPTANCE,
        status=stage_status,
        outputs=[summary_ref, markdown_ref],
        notes=[f"acceptance summary written for {resolved.paper_id}"],
    )
    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="acceptance_summary",
        kind="acceptance-summary",
        ref=summary_ref,
        format="json",
        stage=StageName.ACCEPTANCE.value,
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="acceptance_summary_markdown",
        kind="acceptance-summary-markdown",
        ref=markdown_ref,
        format="markdown",
        stage=StageName.ACCEPTANCE.value,
        private_content=False,
    )
    artifact_index = update_artifact_index(
        artifact_index,
        stage_name=StageName.ACCEPTANCE,
        stage_status=stage_status,
    )
    persist_run_artifacts(
        resolved,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
    return AcceptanceWriteResult(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        status=summary.status.value,
        summary_path=summary_path,
        markdown_path=markdown_path,
    )


def _load_acceptance_batch_manifest(
    path: str | Path,
) -> tuple[str, list[dict[str, str]]]:
    payload = load_json_object(path, "acceptance batch manifest")
    unexpected = sorted(set(payload) - {"schema_version", "batch_id", "runs"})
    if unexpected:
        raise MillefeuilleContractError(
            "acceptance batch manifest has unsupported fields: " + ", ".join(unexpected)
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str):
        raise MillefeuilleContractError(
            "acceptance batch schema_version must be a string"
        )
    if schema_version != ACCEPTANCE_BATCH_MANIFEST_SCHEMA:
        raise MillefeuilleContractError(
            f"unsupported acceptance batch schema_version {schema_version!r}"
        )
    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str):
        raise MillefeuilleContractError("acceptance batch batch_id must be a string")
    if batch_id in {".", ".."} or _SAFE_BATCH_ID.fullmatch(batch_id) is None:
        raise MillefeuilleContractError(
            "batch_id must be traversal-safe and use only letters, numbers, "
            "'.', '_', or '-'"
        )
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise MillefeuilleContractError(
            "acceptance batch runs must be a non-empty array"
        )

    locators: list[dict[str, str]] = []
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict):
            raise MillefeuilleContractError(
                f"acceptance batch run {index} must be an object"
            )
        unexpected = sorted(set(entry) - {"paper_id", "item_key", "run_id"})
        if unexpected:
            raise MillefeuilleContractError(
                f"acceptance batch run {index} has unsupported fields: "
                + ", ".join(unexpected)
            )
        if "run_id" not in entry:
            raise MillefeuilleContractError(
                f"acceptance batch run {index} requires run_id"
            )
        run_id = entry["run_id"]
        has_paper_id = "paper_id" in entry
        has_item_key = "item_key" in entry
        if has_paper_id == has_item_key:
            raise MillefeuilleContractError(
                f"acceptance batch run {index} requires exactly one of "
                "paper_id or item_key"
            )
        locator_field = "paper_id" if has_paper_id else "item_key"
        locator_value = entry[locator_field]
        non_string_fields = [
            field_name
            for field_name, value in (
                ("run_id", run_id),
                (locator_field, locator_value),
            )
            if not isinstance(value, str)
        ]
        if non_string_fields:
            raise MillefeuilleContractError(
                f"acceptance batch run {index} has non-string locator fields: "
                + ", ".join(non_string_fields)
            )
        unsafe_fields = [
            field_name
            for field_name, value in (
                ("run_id", run_id),
                (locator_field, locator_value),
            )
            if _SAFE_BATCH_LOCATOR.fullmatch(value) is None
        ]
        if unsafe_fields:
            raise MillefeuilleContractError(
                f"acceptance batch run {index} has unsafe locator fields: "
                + ", ".join(unsafe_fields)
            )
        locator = {"run_id": run_id, locator_field: locator_value}
        locators.append(locator)
    return batch_id, locators


def _build_acceptance_summary(
    *,
    resolved: ResolvedRunArtifacts,
    handoff_path: str | Path | None,
    duplicate_scan_path: str | Path | None,
) -> AcceptanceSummaryRecord:
    checks: list[AcceptanceCheckRecord] = []
    review_reasons: list[str] = []
    counts: dict[str, int] = {}

    manifest_ref = relative_ref(
        resolved.source_pack_dir / "manifest.json",
        resolved.run_dir,
    )
    checks.append(
        AcceptanceCheckRecord(
            name="source-pack",
            status=AcceptanceCheckStatus.PASSED,
            refs=[manifest_ref],
            notes=["verified source-pack manifest loaded"],
        )
    )
    counts["source_pack"] = 1

    handoff = _build_handoff_context(
        resolved=resolved,
        handoff_path=handoff_path,
        review_reasons=review_reasons,
    )
    checks.append(handoff["check"])
    counts["handoff_rows"] = int(handoff["count"])

    native_check, native_present = _load_optional_check(
        name="extract-native",
        run_dir=resolved.run_dir,
        target_path=resolved.source_pack_dir / NATIVE_EVIDENCE_REF,
        loader=load_native_extraction_sidecar,
        source_hash=resolved.source_hash,
    )
    checks.append(native_check)
    counts["native_extraction"] = int(native_present)

    ocr_check, ocr_present = _load_optional_check(
        name="extract-ocr",
        run_dir=resolved.run_dir,
        target_path=resolved.source_pack_dir / OCR_EVIDENCE_REF,
        loader=load_ocr_extraction_sidecar,
        source_hash=resolved.source_hash,
    )
    checks.append(ocr_check)
    counts["ocr_extraction"] = int(ocr_present)
    if not native_present and not ocr_present:
        review_reasons.append("no extraction evidence is available")

    route_check = _load_required_check(
        name="route",
        run_dir=resolved.run_dir,
        target_path=resolved.source_pack_dir / ROUTE_EVIDENCE_REF,
        loader=load_route_selection_sidecar,
        source_hash=resolved.source_hash,
        review_reasons=review_reasons,
    )
    checks.append(route_check)
    counts["route"] = int(route_check.status == AcceptanceCheckStatus.PASSED)

    structure_check = _load_required_check(
        name="structure",
        run_dir=resolved.run_dir,
        target_path=resolved.source_pack_dir / STRUCTURE_EVIDENCE_REF,
        loader=load_structure_sidecar,
        source_hash=resolved.source_hash,
        review_reasons=review_reasons,
    )
    checks.append(structure_check)
    counts["structure"] = int(structure_check.status == AcceptanceCheckStatus.PASSED)

    summary_check = _load_run_scoped_summary_check(
        resolved=resolved,
        review_reasons=review_reasons,
    )
    checks.append(summary_check)
    counts["summary"] = int(summary_check.status == AcceptanceCheckStatus.PASSED)

    card_check = _load_run_scoped_card_check(
        resolved=resolved,
        review_reasons=review_reasons,
    )
    checks.append(card_check)
    counts["card"] = int(card_check.status == AcceptanceCheckStatus.PASSED)

    index_check, index_payload = _load_run_scoped_index_check(
        resolved=resolved,
        review_reasons=review_reasons,
    )
    checks.append(index_check)
    counts["index"] = int(index_check.status == AcceptanceCheckStatus.PASSED)

    duplicate_scan = _build_duplicate_scan_context(
        resolved=resolved,
        duplicate_scan_path=duplicate_scan_path,
        index_payload=index_payload,
        review_reasons=review_reasons,
    )
    checks.append(duplicate_scan["check"])
    counts["duplicate_scan"] = int(
        duplicate_scan["check"].status == AcceptanceCheckStatus.PASSED
    )

    stage_statuses = {
        stage.name.value: stage.status.value for stage in resolved.stage_manifest.stages
    }
    downstream_stages = {
        StageName.ACCEPTANCE.value,
        StageName.CLASSIFY.value,
        StageName.WRITEBACK.value,
        StageName.RELEASE.value,
    }
    counts["passed_stages_before_acceptance"] = sum(
        1
        for stage_name, status in stage_statuses.items()
        if stage_name not in downstream_stages and status == StageStatus.PASSED.value
    )
    status = (
        AcceptanceStatus.PASS if not review_reasons else AcceptanceStatus.NEEDS_REVIEW
    )
    return AcceptanceSummaryRecord(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        source_pack_ref=relative_ref(resolved.source_pack_dir, resolved.run_dir),
        status=status,
        counts=counts,
        checks=checks,
        handoff=handoff["details"],
        duplicate_scan=duplicate_scan["details"],
        review_reasons=review_reasons,
    )


def _build_handoff_context(
    *,
    resolved: ResolvedRunArtifacts,
    handoff_path: str | Path | None,
    review_reasons: list[str],
) -> dict[str, Any]:
    if handoff_path is None:
        review_reasons.append("handoff evidence was not supplied")
        return {
            "count": 0,
            "details": {
                "matched": False,
                "match_count": 0,
            },
            "check": AcceptanceCheckRecord(
                name="handoff",
                status=AcceptanceCheckStatus.NEEDS_REVIEW,
                notes=["acceptance requires explicit handoff evidence"],
            ),
        }

    rows = load_jsonl_records(handoff_path, "handoff evidence")
    source_identity = resolved.artifact_index.source_identity
    attachment_key = source_identity.get("zotero_attachment_key")
    matches = [
        row
        for row in rows
        if row.get("item_key") == source_identity.get("zotero_item_key")
        and (attachment_key is None or row.get("attachment_key") == attachment_key)
    ]
    matched = len(matches) == 1
    if not matched:
        review_reasons.append(
            f"handoff evidence match count is {len(matches)} instead of 1"
        )
    refs = [relative_ref(Path(handoff_path), resolved.run_dir)]
    details = {
        "matched": matched,
        "match_count": len(matches),
    }
    if matches:
        details["canonical_filename"] = matches[0].get("canonical_filename")
        details["verification_strength"] = matches[0].get("verification_strength")
        details["sha256_present"] = matches[0].get("sha256") is not None
    return {
        "count": len(matches),
        "details": details,
        "check": AcceptanceCheckRecord(
            name="handoff",
            status=(
                AcceptanceCheckStatus.PASSED
                if matched
                else AcceptanceCheckStatus.NEEDS_REVIEW
            ),
            refs=refs,
            notes=["matched handoff evidence"]
            if matched
            else ["handoff drift detected"],
        ),
    }


def _build_duplicate_scan_context(
    *,
    resolved: ResolvedRunArtifacts,
    duplicate_scan_path: str | Path | None,
    index_payload: dict[str, Any],
    review_reasons: list[str],
) -> dict[str, Any]:
    duplicate_scan = dict(index_payload.get("duplicate_scan") or {})
    refs: list[str] = []
    if duplicate_scan_path is not None:
        refs.append(relative_ref(Path(duplicate_scan_path), resolved.run_dir))
        records = load_jsonl_records(duplicate_scan_path, "duplicate scan evidence")
        matches = [
            record
            for record in records
            if record.get("paper_id") == resolved.paper_id
            or record.get("canonical_filename")
            == resolved.artifact_index.source_identity.get("canonical_filename")
        ]
        if matches:
            duplicate_scan = dict(matches[0])
            duplicate_scan["match_count"] = len(matches)
    matched_existing = bool(duplicate_scan.get("matched_existing"))
    if matched_existing:
        review_reasons.append("duplicate scan flagged an existing match")
    check_status = (
        AcceptanceCheckStatus.NEEDS_REVIEW
        if matched_existing
        else AcceptanceCheckStatus.PASSED
    )
    notes = (
        ["duplicate scan flagged an existing match"]
        if matched_existing
        else ["duplicate scan indicates no blocking duplicate"]
    )
    return {
        "details": duplicate_scan,
        "check": AcceptanceCheckRecord(
            name="duplicate-scan",
            status=check_status,
            refs=refs,
            notes=notes,
        ),
    }


def _load_optional_check(
    *,
    name: str,
    run_dir: Path,
    target_path: Path,
    loader: Any,
    source_hash: str,
) -> tuple[AcceptanceCheckRecord, bool]:
    if not target_path.is_file():
        return (
            AcceptanceCheckRecord(
                name=name,
                status=AcceptanceCheckStatus.SKIPPED,
                notes=["artifact not present"],
            ),
            False,
        )
    payload = loader(target_path)
    status = (
        AcceptanceCheckStatus.PASSED
        if payload.get("source_hash") == source_hash
        else AcceptanceCheckStatus.NEEDS_REVIEW
    )
    return (
        AcceptanceCheckRecord(
            name=name,
            status=status,
            refs=[relative_ref(target_path, run_dir)],
            notes=["artifact verified"]
            if status == AcceptanceCheckStatus.PASSED
            else ["source hash drift"],
        ),
        True,
    )


def _load_required_check(
    *,
    name: str,
    run_dir: Path,
    target_path: Path,
    loader: Any,
    source_hash: str,
    review_reasons: list[str],
) -> AcceptanceCheckRecord:
    check, present = _load_optional_check(
        name=name,
        run_dir=run_dir,
        target_path=target_path,
        loader=loader,
        source_hash=source_hash,
    )
    if not present or check.status != AcceptanceCheckStatus.PASSED:
        review_reasons.append(f"{name} evidence is missing or drifted")
        return AcceptanceCheckRecord(
            name=name,
            status=AcceptanceCheckStatus.NEEDS_REVIEW,
            refs=list(check.refs),
            notes=list(check.notes),
        )
    return check


def _load_run_scoped_summary_check(
    *,
    resolved: ResolvedRunArtifacts,
    review_reasons: list[str],
) -> AcceptanceCheckRecord:
    summary_path = resolved.run_dir / SUMMARY_ARTIFACT_REF
    if not summary_path.is_file():
        review_reasons.append("summary artifact is missing")
        return AcceptanceCheckRecord(
            name="summarize",
            status=AcceptanceCheckStatus.NEEDS_REVIEW,
            notes=["hierarchical summary not found"],
        )
    payload = load_hierarchical_summary(summary_path)
    status = (
        AcceptanceCheckStatus.PASSED
        if payload["paper_id"] == resolved.paper_id
        and payload["run_id"] == resolved.run_id
        else AcceptanceCheckStatus.NEEDS_REVIEW
    )
    if status != AcceptanceCheckStatus.PASSED:
        review_reasons.append("summary identity drift detected")
    return AcceptanceCheckRecord(
        name="summarize",
        status=status,
        refs=[relative_ref(summary_path, resolved.run_dir)],
        notes=["hierarchical summary verified"]
        if status == AcceptanceCheckStatus.PASSED
        else ["summary identity drift"],
        details={"summary_count": len(payload["summaries"])},
    )


def _load_run_scoped_card_check(
    *,
    resolved: ResolvedRunArtifacts,
    review_reasons: list[str],
) -> AcceptanceCheckRecord:
    card_path = resolved.run_dir / CARD_JSON_REF
    if not card_path.is_file():
        review_reasons.append("paper card artifact is missing")
        return AcceptanceCheckRecord(
            name="card",
            status=AcceptanceCheckStatus.NEEDS_REVIEW,
            notes=["paper card not found"],
        )
    payload = load_paper_card(card_path)
    identity_error: str | None = None
    try:
        validate_paper_card_identity(
            payload,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
            require_source_hash=True,
        )
    except MillefeuilleContractError as exc:
        identity_error = str(exc)
    status = (
        AcceptanceCheckStatus.PASSED
        if identity_error is None
        else AcceptanceCheckStatus.NEEDS_REVIEW
    )
    if status != AcceptanceCheckStatus.PASSED:
        review_reasons.append(identity_error or "paper card identity drift detected")
    return AcceptanceCheckRecord(
        name="card",
        status=status,
        refs=[relative_ref(card_path, resolved.run_dir)],
        notes=["paper card verified"]
        if status == AcceptanceCheckStatus.PASSED
        else ["paper card identity drift"],
    )


def _load_run_scoped_index_check(
    *,
    resolved: ResolvedRunArtifacts,
    review_reasons: list[str],
) -> tuple[AcceptanceCheckRecord, dict[str, Any]]:
    index_path = resolved.run_dir / INDEX_STATUS_REF
    if not index_path.is_file():
        review_reasons.append("retrieval/index status artifact is missing")
        return (
            AcceptanceCheckRecord(
                name="index",
                status=AcceptanceCheckStatus.NEEDS_REVIEW,
                notes=["retrieval index status not found"],
            ),
            {},
        )
    payload = load_retrieval_index_status(index_path)
    join_error: str | None = None
    try:
        card_artifact, index_artifact = load_and_validate_canonical_card_index(
            card_path=resolved.run_dir / CARD_JSON_REF,
            index_path=index_path,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
            selected_fulltext_path=resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
            summary_path=resolved.run_dir / SUMMARY_ARTIFACT_REF,
        )
        del card_artifact
        payload = index_artifact.payload
    except MillefeuilleContractError as exc:
        join_error = str(exc)
    status = (
        AcceptanceCheckStatus.PASSED
        if join_error is None
        else AcceptanceCheckStatus.NEEDS_REVIEW
    )
    if status != AcceptanceCheckStatus.PASSED:
        review_reasons.append(join_error or "retrieval/index identity drift detected")
    return (
        AcceptanceCheckRecord(
            name="index",
            status=status,
            refs=[relative_ref(index_path, resolved.run_dir)],
            notes=["retrieval index and paper card state verified"]
            if status == AcceptanceCheckStatus.PASSED
            else [join_error or "retrieval index drift"],
            details={"lanes": [lane["lane"] for lane in payload.get("lanes", [])]},
        ),
        payload,
    )


def _render_acceptance_markdown(summary: AcceptanceSummaryRecord) -> str:
    lines = [
        "# Acceptance Summary",
        "",
        f"- Paper: `{summary.paper_id}`",
        f"- Run: `{summary.run_id}`",
        f"- Status: `{summary.status.value}`",
        f"- Source hash: `{summary.source_hash}`",
        "",
        "## Checks",
    ]
    for check in summary.checks:
        refs = ", ".join(f"`{ref}`" for ref in check.refs) if check.refs else "[none]"
        lines.append(f"- `{check.name}`: `{check.status.value}` ({refs})")
        for note in check.notes:
            lines.append(f"  note: {note}")
    if summary.review_reasons:
        lines.extend(["", "## Review Reasons"])
        for reason in summary.review_reasons:
            lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def _render_acceptance_batch_markdown(
    summary: AcceptanceBatchSummaryRecord,
) -> str:
    lines = [
        "# Acceptance Batch Summary",
        "",
        f"- Batch: `{summary.batch_id}`",
        f"- Status: `{summary.status.value}`",
        f"- Runs: `{summary.counts['runs']}`",
        f"- Passed: `{summary.counts['passed']}`",
        f"- Needs review: `{summary.counts['needs_review']}`",
        "",
        "## Runs",
    ]
    for run in summary.runs:
        lines.append(
            f"- `{run.paper_id}` / `{run.run_id}`: `{run.status.value}` "
            f"(`{run.summary_ref}`)"
        )
        for reason in run.review_reasons:
            lines.append(f"  review: {reason}")
    return "\n".join(lines) + "\n"
