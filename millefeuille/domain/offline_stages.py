"""Fixture-only stage adapters for resumable offline Millefeuille runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from millefeuille.domain.acceptance import ACCEPTANCE_SUMMARY_REF
from millefeuille.domain.artifacts import ArtifactIndex
from millefeuille.domain.card_fixtures import (
    CARD_JSON_REF,
    CARD_MARKDOWN_REF,
    load_card_fixture_evidence_batch,
    load_paper_card,
    write_cards_from_evidence,
)
from millefeuille.domain.card_index_contract import (
    load_and_validate_canonical_card_index,
    validate_paper_card_identity,
)
from millefeuille.domain.classification import (
    CLASSIFICATION_PLAN_REF,
    DECISION_DIR_REF,
)
from millefeuille.domain.extraction_fixtures import (
    NATIVE_EVIDENCE_REF,
    NATIVE_MARKDOWN_REF,
    OCR_EVIDENCE_REF,
    OCR_MARKDOWN_REF,
    load_native_extraction_evidence_batch,
    load_native_extraction_sidecar,
    load_ocr_extraction_evidence_batch,
    load_ocr_extraction_sidecar,
    write_native_extractions_from_evidence,
    write_ocr_extractions_from_evidence,
)
from millefeuille.domain.index_fixtures import (
    INDEX_STATUS_REF,
    load_index_fixture_evidence_batch,
    load_retrieval_index_status,
    write_indexes_from_evidence,
)
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    StageName,
    StageStatus,
)
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    load_route_selection_evidence_batch,
    load_route_selection_sidecar,
    write_route_selections_from_evidence,
)
from millefeuille.domain.source_packs import paper_id_for_zotero_item_key
from millefeuille.domain.stage_runtime import (
    ResolvedRunArtifacts,
    load_json_object,
    persist_run_artifacts,
    relative_ref,
    resolve_run_artifacts,
    stage_status_map,
    update_artifact_index,
    upsert_artifact_record,
    upsert_stage_record,
)
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    load_structure_evidence_batch,
    load_structure_sidecar,
    write_structures_from_evidence,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    load_hierarchical_summary,
    load_summary_fixture_evidence_batch,
    write_summaries_from_evidence,
)
from millefeuille.domain.writeback import WRITEBACK_PLAN_REF

OFFLINE_FIXTURE_STAGES = frozenset(
    {
        StageName.EXTRACT_NATIVE,
        StageName.EXTRACT_OCR,
        StageName.ROUTE,
        StageName.STRUCTURE,
        StageName.SUMMARIZE,
        StageName.CARD,
        StageName.INDEX,
    }
)


@dataclass(frozen=True)
class OfflineStageWriteResult:
    stage: str
    paper_id: str
    run_id: str
    write_status: str
    outputs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "write_status": self.write_status,
            "outputs": list(self.outputs),
        }


def write_offline_fixture_stage(
    *,
    stage: str | StageName,
    evidence_path: str | Path,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
) -> OfflineStageWriteResult:
    stage_name = _coerce_fixture_stage(stage)
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    evidence = Path(evidence_path)
    records, results = _write_stage(
        stage_name=stage_name,
        evidence_path=evidence,
        source_pack_root=source_pack_root,
        run_id=resolved.run_id,
        expected_paper_id=resolved.paper_id,
        artifact_run_dir=resolved.run_dir,
    )
    _require_single_paper_record(
        stage_name=stage_name,
        records=records,
        results=results,
        expected_paper_id=resolved.paper_id,
        expected_run_id=resolved.run_id,
    )

    result = results[0]
    refreshed = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=resolved.run_id,
        paper_id=resolved.paper_id,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    output_records = _output_records(stage_name, result)
    output_refs = [
        relative_ref(record[3], refreshed.run_dir) for record in output_records
    ]
    stage_manifest = upsert_stage_record(
        refreshed.stage_manifest,
        name=stage_name,
        status=StageStatus.PASSED.value,
        inputs=[relative_ref(evidence, refreshed.run_dir)],
        outputs=output_refs,
        notes=["fixture-only offline stage completed without provider calls"],
    )
    artifact_index = refreshed.artifact_index
    for name, kind, format_name, output_path, private_content in output_records:
        artifact_index = upsert_artifact_record(
            artifact_index,
            name=name,
            kind=kind,
            ref=relative_ref(output_path, refreshed.run_dir),
            format=format_name,
            stage=stage_name.value,
            private_content=private_content,
        )
    artifact_index = update_artifact_index(
        artifact_index,
        stage_name=stage_name,
        stage_status=StageStatus.PASSED.value,
    )
    if stage_name == StageName.INDEX:
        artifact_index = _update_index_lanes(
            artifact_index,
            load_retrieval_index_status(result.index_status_path),
        )
    persist_run_artifacts(
        refreshed,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
    return OfflineStageWriteResult(
        stage=stage_name.value,
        paper_id=refreshed.paper_id,
        run_id=refreshed.run_id,
        write_status=str(result.status),
        outputs=output_refs,
    )


def can_resume_stage(resolved: ResolvedRunArtifacts, stage: str | StageName) -> bool:
    """Return true only when a passed stage still has valid, matching outputs."""

    stage_name = StageName(str(stage))
    if stage_status_map(resolved.stage_manifest).get(stage_name.value) != (
        StageStatus.PASSED.value
    ):
        return False

    if stage_name == StageName.EXTRACT_NATIVE:
        payload = load_native_extraction_sidecar(
            resolved.source_pack_dir / NATIVE_EVIDENCE_REF
        )
        _require_source_hash(payload, resolved, "native extraction")
        _require_file(resolved.source_pack_dir / NATIVE_MARKDOWN_REF)
    elif stage_name == StageName.EXTRACT_OCR:
        payload = load_ocr_extraction_sidecar(
            resolved.source_pack_dir / OCR_EVIDENCE_REF
        )
        _require_source_hash(payload, resolved, "OCR extraction")
        _require_file(resolved.source_pack_dir / OCR_MARKDOWN_REF)
    elif stage_name == StageName.ROUTE:
        payload = load_route_selection_sidecar(
            resolved.source_pack_dir / ROUTE_EVIDENCE_REF
        )
        _require_source_hash(payload, resolved, "route selection")
        _require_file(resolved.source_pack_dir / ROUTE_MARKDOWN_REF)
    elif stage_name == StageName.STRUCTURE:
        payload = load_structure_sidecar(
            resolved.source_pack_dir / STRUCTURE_EVIDENCE_REF
        )
        _require_source_hash(payload, resolved, "structure")
    elif stage_name == StageName.SUMMARIZE:
        payload = load_hierarchical_summary(resolved.run_dir / SUMMARY_ARTIFACT_REF)
        _require_paper_run(payload, resolved, "hierarchical summary")
    elif stage_name == StageName.CARD:
        payload = load_paper_card(resolved.run_dir / CARD_JSON_REF)
        validate_paper_card_identity(
            payload,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
        )
        _require_file(resolved.run_dir / CARD_MARKDOWN_REF)
    elif stage_name == StageName.INDEX:
        load_and_validate_canonical_card_index(
            card_path=resolved.run_dir / CARD_JSON_REF,
            index_path=resolved.run_dir / INDEX_STATUS_REF,
            paper_id=resolved.paper_id,
            run_id=resolved.run_id,
            source_hash=resolved.source_hash,
            selected_fulltext_path=resolved.source_pack_dir / ROUTE_MARKDOWN_REF,
            summary_path=resolved.run_dir / SUMMARY_ARTIFACT_REF,
        )
    elif stage_name == StageName.ACCEPTANCE:
        payload = load_json_object(
            resolved.run_dir / ACCEPTANCE_SUMMARY_REF,
            "acceptance summary",
        )
        _require_paper_run(payload, resolved, "acceptance summary")
        _require_source_hash(payload, resolved, "acceptance summary")
        if payload.get("status") != "pass":
            raise MillefeuilleContractError("acceptance summary is not passed")
    elif stage_name == StageName.CLASSIFY:
        plan = load_json_object(
            resolved.run_dir / CLASSIFICATION_PLAN_REF,
            "classification plan",
        )
        if plan.get("run_id") != resolved.run_id:
            raise MillefeuilleContractError("classification plan run_id drift")
        decision = load_json_object(
            resolved.run_dir / DECISION_DIR_REF / f"{resolved.paper_id}.json",
            "classification decision",
        )
        _require_paper_run(decision, resolved, "classification decision")
        _require_source_hash(decision, resolved, "classification decision")
        if decision.get("status") != "classified":
            raise MillefeuilleContractError("classification decision is not classified")
    elif stage_name == StageName.WRITEBACK:
        payload = load_json_object(
            resolved.run_dir / WRITEBACK_PLAN_REF,
            "writeback plan",
        )
        _require_paper_run(payload, resolved, "writeback plan")
        _require_source_hash(payload, resolved, "writeback plan")
        if payload.get("mode") != "preview" or payload.get("status") != "previewed":
            raise MillefeuilleContractError("writeback plan is not a valid preview")
    else:
        return False
    return True


def _write_stage(
    *,
    stage_name: StageName,
    evidence_path: Path,
    source_pack_root: str | Path,
    run_id: str,
    expected_paper_id: str,
    artifact_run_dir: Path,
) -> tuple[list[Any], list[Any]]:
    if stage_name == StageName.EXTRACT_NATIVE:
        records = load_native_extraction_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_native_extractions_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
        )
    elif stage_name == StageName.EXTRACT_OCR:
        records = load_ocr_extraction_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_ocr_extractions_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
        )
    elif stage_name == StageName.ROUTE:
        records = load_route_selection_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_route_selections_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
        )
    elif stage_name == StageName.STRUCTURE:
        records = load_structure_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_structures_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
        )
    elif stage_name == StageName.SUMMARIZE:
        records = load_summary_fixture_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_summaries_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
            run_id=run_id,
            artifact_run_dir=artifact_run_dir,
        )
    elif stage_name == StageName.CARD:
        records = load_card_fixture_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_cards_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
            run_id=run_id,
            artifact_run_dir=artifact_run_dir,
        )
    elif stage_name == StageName.INDEX:
        records = load_index_fixture_evidence_batch(evidence_path)
        _require_record_before_write(stage_name, records, expected_paper_id)
        results = write_indexes_from_evidence(
            evidence_path=evidence_path,
            source_pack_root=source_pack_root,
            run_id=run_id,
            artifact_run_dir=artifact_run_dir,
        )
    else:  # pragma: no cover - guarded by _coerce_fixture_stage
        raise MillefeuilleContractError(
            f"unsupported offline fixture stage {stage_name.value!r}"
        )
    return records, results


def _output_records(
    stage_name: StageName, result: Any
) -> list[tuple[str, str, str, Path, bool]]:
    if stage_name == StageName.EXTRACT_NATIVE:
        return [
            (
                "native_extraction_evidence",
                "native-extraction-evidence",
                "json",
                result.evidence_path,
                False,
            ),
            (
                "native_extraction_markdown",
                "native-extraction-markdown",
                "markdown",
                result.markdown_path,
                True,
            ),
        ]
    if stage_name == StageName.EXTRACT_OCR:
        return [
            (
                "ocr_extraction_evidence",
                "ocr-extraction-evidence",
                "json",
                result.evidence_path,
                False,
            ),
            (
                "ocr_extraction_markdown",
                "ocr-extraction-markdown",
                "markdown",
                result.markdown_path,
                True,
            ),
        ]
    if stage_name == StageName.ROUTE:
        return [
            (
                "route_evidence",
                "route-selection-evidence",
                "json",
                result.evidence_path,
                False,
            ),
            (
                "selected_fulltext",
                "selected-fulltext",
                "markdown",
                result.markdown_path,
                True,
            ),
        ]
    if stage_name == StageName.STRUCTURE:
        records = [
            (
                "structure_evidence",
                "structure-evidence",
                "json",
                result.evidence_path,
                False,
            )
        ]
        if result.outline_path is not None:
            records.append(
                (
                    "structure_outline",
                    "structure-outline",
                    "markdown",
                    result.outline_path,
                    True,
                )
            )
        return records
    if stage_name == StageName.SUMMARIZE:
        return [
            (
                "hierarchical_summary",
                "hierarchical-summary",
                "json",
                result.summary_path,
                False,
            ),
            (
                "summary_texts",
                "summary-texts",
                "directory",
                result.summary_text_dir,
                True,
            ),
        ]
    if stage_name == StageName.CARD:
        return [
            ("paper_card_json", "paper-card", "json", result.card_json_path, False),
            (
                "paper_card_markdown",
                "paper-card-markdown",
                "markdown",
                result.card_markdown_path,
                True,
            ),
        ]
    if stage_name == StageName.INDEX:
        return [
            (
                "retrieval_index_status",
                "retrieval-index-status",
                "json",
                result.index_status_path,
                False,
            )
        ]
    raise MillefeuilleContractError(
        f"unsupported offline fixture stage {stage_name.value!r}"
    )


def _require_one_record_before_write(stage_name: StageName, records: list[Any]) -> None:
    if len(records) != 1:
        raise MillefeuilleContractError(
            f"{stage_name.value} requires exactly one evidence record per CLI run"
        )


def _require_record_before_write(
    stage_name: StageName,
    records: list[Any],
    expected_paper_id: str,
) -> None:
    _require_one_record_before_write(stage_name, records)
    record = records[0]
    record_paper_id = record.paper_id or paper_id_for_zotero_item_key(record.item_key)
    if record_paper_id != expected_paper_id:
        raise MillefeuilleContractError(
            f"{stage_name.value} evidence paper_id drift: "
            f"expected {expected_paper_id!r}, got {record_paper_id!r}"
        )


def _require_single_paper_record(
    *,
    stage_name: StageName,
    records: list[Any],
    results: list[Any],
    expected_paper_id: str,
    expected_run_id: str,
) -> None:
    _require_one_record_before_write(stage_name, records)
    if len(results) != 1:
        raise MillefeuilleContractError(
            f"{stage_name.value} did not produce exactly one write result"
        )
    record = records[0]
    record_paper_id = record.paper_id or paper_id_for_zotero_item_key(record.item_key)
    if record_paper_id != expected_paper_id:
        raise MillefeuilleContractError(
            f"{stage_name.value} evidence paper_id drift: "
            f"expected {expected_paper_id!r}, got {record_paper_id!r}"
        )
    result = results[0]
    if result.paper_id != expected_paper_id:
        raise MillefeuilleContractError(f"{stage_name.value} result paper_id drift")
    result_run_id = getattr(result, "run_id", expected_run_id)
    if result_run_id != expected_run_id:
        raise MillefeuilleContractError(f"{stage_name.value} result run_id drift")


def _coerce_fixture_stage(stage: str | StageName) -> StageName:
    try:
        stage_name = stage if isinstance(stage, StageName) else StageName(str(stage))
    except ValueError as exc:
        raise MillefeuilleContractError(
            f"unsupported offline fixture stage {stage!r}"
        ) from exc
    if stage_name not in OFFLINE_FIXTURE_STAGES:
        raise MillefeuilleContractError(
            f"unsupported offline fixture stage {stage_name.value!r}"
        )
    return stage_name


def _update_index_lanes(
    artifact_index: ArtifactIndex,
    index_payload: dict[str, Any],
) -> ArtifactIndex:
    payload = artifact_index.to_dict()
    by_lane = {record["lane"]: dict(record) for record in payload["indexes"]}
    for record in index_payload.get("lanes", []):
        projected = {
            "lane": str(record["lane"]),
            "status": str(record["status"]),
        }
        skip_reason = record.get("skip_reason")
        if isinstance(skip_reason, str) and skip_reason.strip():
            projected["skip_reason"] = skip_reason
        result_ref = record.get("result_ref")
        if isinstance(result_ref, str) and result_ref.strip():
            projected["result_ref"] = result_ref
        by_lane[projected["lane"]] = projected
    order = ["openkb", "pageindex", "condb", "chatindex"]
    payload["indexes"] = [by_lane[lane] for lane in order if lane in by_lane]
    payload["indexes"].extend(
        record for lane, record in by_lane.items() if lane not in order
    )
    return ArtifactIndex.from_dict(payload)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise MillefeuilleContractError(f"stage output is missing: {path}")


def _require_paper_run(
    payload: dict[str, Any],
    resolved: ResolvedRunArtifacts,
    label: str,
) -> None:
    if payload.get("paper_id") != resolved.paper_id:
        raise MillefeuilleContractError(f"{label} paper_id drift")
    if payload.get("run_id") != resolved.run_id:
        raise MillefeuilleContractError(f"{label} run_id drift")


def _require_source_hash(
    payload: dict[str, Any],
    resolved: ResolvedRunArtifacts,
    label: str,
) -> None:
    if payload.get("source_hash") != resolved.source_hash:
        raise MillefeuilleContractError(f"{label} source_hash drift")
