"""Artifact writer helpers for dry-run Millefeuille evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from millefeuille.domain.artifacts import ArtifactIndex, write_artifact_index
from millefeuille.domain.card_fixtures import (
    CARD_JSON_REF,
    CARD_MARKDOWN_REF,
    load_paper_card,
)
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.extraction_fixtures import (
    NATIVE_EVIDENCE_REF,
    NATIVE_MARKDOWN_REF,
    OCR_EVIDENCE_REF,
    OCR_MARKDOWN_REF,
    load_native_extraction_sidecar,
    load_ocr_extraction_sidecar,
)
from millefeuille.domain.millefeuille import (
    ManualGate,
    RunMode,
    StageManifest,
    StageName,
    StageRecord,
    StageStatus,
)
from millefeuille.domain.models import DiscoveredItem, OpenKBHandoffRow
from millefeuille.domain.route_fixtures import (
    ROUTE_EVIDENCE_REF,
    ROUTE_MARKDOWN_REF,
    load_route_selection_sidecar,
)
from millefeuille.domain.source_packs import (
    DEFAULT_SOURCE_PACK_ROOT,
    SOURCE_PACK_ARTIFACT_ROOT,
    load_source_pack_manifest_source_hash,
    paper_id_for_zotero_item_key,
)
from millefeuille.domain.structure_fixtures import (
    STRUCTURE_EVIDENCE_REF,
    STRUCTURE_OUTLINE_REF,
    load_structure_sidecar,
)
from millefeuille.domain.summary_fixtures import (
    SUMMARY_ARTIFACT_REF,
    SUMMARY_TEXT_DIR_REF,
    load_hierarchical_summary,
)


@dataclass(frozen=True)
class ArtifactWriteResult:
    paper_id: str
    run_id: str
    run_dir: Path
    artifact_index_path: Path
    stage_manifest_path: Path


@dataclass(frozen=True)
class ArtifactRunContext:
    run_dir: Path
    source_pack_ref: str
    source_pack_manifest_ref: str | None = None
    source_pack_hash: str | None = None
    source_pack_verified: bool = False
    native_extraction_evidence_ref: str | None = None
    native_extraction_markdown_ref: str | None = None
    ocr_extraction_evidence_ref: str | None = None
    ocr_extraction_markdown_ref: str | None = None
    route_evidence_ref: str | None = None
    route_markdown_ref: str | None = None
    structure_evidence_ref: str | None = None
    structure_outline_ref: str | None = None
    summary_artifact_ref: str | None = None
    summary_text_dir_ref: str | None = None
    paper_card_json_ref: str | None = None
    paper_card_markdown_ref: str | None = None


def default_artifact_run_id(now: datetime | None = None) -> str:
    timestamp = now or datetime.now(UTC)
    return "dry-run-" + timestamp.strftime("%Y%m%dT%H%M%SZ")


def write_dry_run_artifacts(
    *,
    items: list[DiscoveredItem],
    handoff_rows: list[OpenKBHandoffRow],
    config: ArtifactExportConfig,
    handoff_enabled: bool = False,
) -> list[ArtifactWriteResult]:
    """Write per-paper dry-run artifact indexes and stage manifests.

    The writer records discovered Zotero identity, optional handoff preview
    evidence, and explicit manual gates for later PDF recovery/source-pack
    creation. It does not write source packs, OpenKB indexes, Zotero tags, PDF
    payloads, OCR output, or model output.
    """
    if not config.enabled:
        return []
    if config.artifact_root is None:
        raise ValueError("artifact_root must be configured when artifacts are enabled")

    run_id = str(config.run_id).strip() if config.run_id else default_artifact_run_id()
    rows_by_item: dict[str, list[OpenKBHandoffRow]] = {}
    for row in handoff_rows:
        rows_by_item.setdefault(row.item_key, []).append(row)

    planned_runs: list[
        tuple[DiscoveredItem, str, list[OpenKBHandoffRow], ArtifactRunContext]
    ] = []
    for item in items:
        paper_id = paper_id_for_item(item)
        item_rows = rows_by_item.get(item.key, [])
        run_context = resolve_artifact_run_context(
            item=item,
            config=config,
            run_id=run_id,
        )
        planned_runs.append((item, paper_id, item_rows, run_context))

    results: list[ArtifactWriteResult] = []
    for item, paper_id, item_rows, run_context in planned_runs:
        run_dir = run_context.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

        stage_manifest = build_dry_run_stage_manifest(
            item=item,
            rows=item_rows,
            run_id=run_id,
            handoff_enabled=handoff_enabled,
            source_pack_verified=run_context.source_pack_verified,
            native_extraction_ready=(
                run_context.native_extraction_evidence_ref is not None
            ),
            ocr_extraction_ready=run_context.ocr_extraction_evidence_ref is not None,
            route_ready=run_context.route_evidence_ref is not None,
            structure_ready=run_context.structure_evidence_ref is not None,
            structure_outline_ready=run_context.structure_outline_ref is not None,
            summarize_ready=run_context.summary_artifact_ref is not None,
            card_ready=run_context.paper_card_json_ref is not None,
        )
        stage_manifest_path = run_dir / "stage-manifest.json"
        write_stage_manifest(stage_manifest, stage_manifest_path)

        artifact_index = build_dry_run_artifact_index(
            item=item,
            rows=item_rows,
            run_id=run_id,
            run_dir=run_dir,
            stage_manifest=stage_manifest,
            source_pack_ref=run_context.source_pack_ref,
            source_pack_manifest_ref=run_context.source_pack_manifest_ref,
            source_pack_hash=run_context.source_pack_hash,
            native_extraction_evidence_ref=(
                run_context.native_extraction_evidence_ref
            ),
            native_extraction_markdown_ref=(
                run_context.native_extraction_markdown_ref
            ),
            ocr_extraction_evidence_ref=run_context.ocr_extraction_evidence_ref,
            ocr_extraction_markdown_ref=run_context.ocr_extraction_markdown_ref,
            route_evidence_ref=run_context.route_evidence_ref,
            route_markdown_ref=run_context.route_markdown_ref,
            structure_evidence_ref=run_context.structure_evidence_ref,
            structure_outline_ref=run_context.structure_outline_ref,
            summary_artifact_ref=run_context.summary_artifact_ref,
            summary_text_dir_ref=run_context.summary_text_dir_ref,
            paper_card_json_ref=run_context.paper_card_json_ref,
            paper_card_markdown_ref=run_context.paper_card_markdown_ref,
        )
        artifact_index_path = run_dir / "artifact-index.json"
        write_artifact_index(artifact_index, artifact_index_path)

        results.append(
            ArtifactWriteResult(
                paper_id=paper_id,
                run_id=run_id,
                run_dir=run_dir,
                artifact_index_path=artifact_index_path,
                stage_manifest_path=stage_manifest_path,
            )
        )
    return results


def resolve_artifact_run_context(
    *,
    item: DiscoveredItem,
    config: ArtifactExportConfig,
    run_id: str,
) -> ArtifactRunContext:
    if config.artifact_root is None:
        raise ValueError("artifact_root must be configured when artifacts are enabled")

    paper_id = paper_id_for_item(item)
    artifact_root = str(config.artifact_root).strip()
    if artifact_root != SOURCE_PACK_ARTIFACT_ROOT:
        run_dir = Path(config.artifact_root) / paper_id / run_id
        return ArtifactRunContext(
            run_dir=run_dir,
            source_pack_ref=f"source-packs/zotero/{paper_id}",
        )

    source_pack_root = config.source_pack_root or DEFAULT_SOURCE_PACK_ROOT
    source_pack_dir = Path(source_pack_root) / "zotero" / paper_id
    source_pack_manifest = source_pack_dir / "manifest.json"
    if not source_pack_manifest.is_file():
        raise ValueError(
            "export.artifacts.artifact_root=source-pack requires an existing "
            f"source-pack manifest at {source_pack_manifest}"
        )
    try:
        source_pack_hash = load_source_pack_manifest_source_hash(source_pack_manifest)
    except ValueError as exc:
        raise ValueError(
            "export.artifacts.artifact_root=source-pack requires a source-pack "
            f"manifest with a verified source_hash at {source_pack_manifest}: {exc}"
        ) from exc

    run_dir = source_pack_dir / "analyses" / "millefeuille" / run_id
    manifest_ref = _relative_ref(source_pack_manifest, run_dir)
    extraction_refs = _resolve_extraction_refs(
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        expected_source_hash=source_pack_hash,
    )
    summary_refs = _resolve_summary_refs(
        run_dir=run_dir,
        paper_id=paper_id,
        run_id=run_id,
    )
    card_refs = _resolve_card_refs(
        run_dir=run_dir,
        paper_id=paper_id,
        expected_source_hash=source_pack_hash,
    )
    return ArtifactRunContext(
        run_dir=run_dir,
        source_pack_ref=str(source_pack_dir),
        source_pack_manifest_ref=manifest_ref,
        source_pack_hash=source_pack_hash,
        source_pack_verified=True,
        native_extraction_evidence_ref=extraction_refs["native_evidence_ref"],
        native_extraction_markdown_ref=extraction_refs["native_markdown_ref"],
        ocr_extraction_evidence_ref=extraction_refs["ocr_evidence_ref"],
        ocr_extraction_markdown_ref=extraction_refs["ocr_markdown_ref"],
        route_evidence_ref=extraction_refs["route_evidence_ref"],
        route_markdown_ref=extraction_refs["route_markdown_ref"],
        structure_evidence_ref=extraction_refs["structure_evidence_ref"],
        structure_outline_ref=extraction_refs["structure_outline_ref"],
        summary_artifact_ref=summary_refs["summary_artifact_ref"],
        summary_text_dir_ref=summary_refs["summary_text_dir_ref"],
        paper_card_json_ref=card_refs["paper_card_json_ref"],
        paper_card_markdown_ref=card_refs["paper_card_markdown_ref"],
    )


def paper_id_for_item(item: DiscoveredItem) -> str:
    return paper_id_for_zotero_item_key(item.key)


def write_stage_manifest(manifest: StageManifest, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_dry_run_stage_manifest(
    *,
    item: DiscoveredItem,
    rows: list[OpenKBHandoffRow],
    run_id: str,
    handoff_enabled: bool,
    source_pack_verified: bool = False,
    native_extraction_ready: bool = False,
    ocr_extraction_ready: bool = False,
    route_ready: bool = False,
    structure_ready: bool = False,
    structure_outline_ready: bool = False,
    summarize_ready: bool = False,
    card_ready: bool = False,
) -> StageManifest:
    has_pdf = bool(rows)
    manual_gates = []
    if has_pdf and not source_pack_verified:
        manual_gates = [ManualGate.PDF_RECOVERY, ManualGate.SOURCE_PACK_WRITE]
    downstream_has_source = has_pdf or source_pack_verified
    stages = [
        StageRecord(
            name=StageName.DISCOVER,
            status=StageStatus.PASSED,
            outputs=["artifact-index.json", "stage-manifest.json"],
            notes=[f"dry-run discovered Zotero item {item.key}"],
        ),
        StageRecord(
            name=StageName.HANDOFF,
            status=StageStatus.PASSED if has_pdf else StageStatus.SKIPPED,
            inputs=[f"zotero:item:{item.key}"],
            outputs=["openkb-millefeuille-handoff-preview"]
            if has_pdf
            else [],
            notes=[
                f"{len(rows)} PDF handoff row(s) available for preview"
                if has_pdf
                else (
                    "handoff enabled but no PDF attachment rows"
                    if handoff_enabled
                    else "handoff export not enabled for this dry-run"
                )
            ],
        ),
        StageRecord(
            name=StageName.RECOVER,
            status=(
                StageStatus.SKIPPED
                if source_pack_verified
                else StageStatus.MANUAL_GATE
                if has_pdf
                else StageStatus.SKIPPED
            ),
            inputs=["openkb-millefeuille-handoff-preview"] if has_pdf else [],
            manual_gate_required=has_pdf and not source_pack_verified,
            gate=ManualGate.PDF_RECOVERY
            if has_pdf and not source_pack_verified
            else None,
            notes=(
                ["source-pack manifest already exists; PDF recovery not performed"]
                if source_pack_verified
                else ["PDF recovery not performed by dry-run artifact writer"]
                if has_pdf
                else ["no PDF rows to recover"]
            ),
        ),
        StageRecord(
            name=StageName.SOURCE_PACK,
            status=(
                StageStatus.PASSED
                if source_pack_verified
                else StageStatus.MANUAL_GATE
                if has_pdf
                else StageStatus.SKIPPED
            ),
            inputs=(
                ["source-pack manifest"]
                if source_pack_verified
                else ["verified recovered PDF bytes"]
                if has_pdf
                else []
            ),
            outputs=["source-pack manifest"] if source_pack_verified else [],
            manual_gate_required=has_pdf and not source_pack_verified,
            gate=ManualGate.SOURCE_PACK_WRITE
            if has_pdf and not source_pack_verified
            else None,
            notes=(
                ["existing source-pack manifest verified for artifact-root lane"]
                if source_pack_verified
                else ["source-pack creation intentionally not performed"]
                if has_pdf
                else ["no source-pack planned without a PDF row"]
            ),
        ),
    ]
    for stage_name in (
        StageName.EXTRACT_NATIVE,
        StageName.EXTRACT_OCR,
        StageName.ROUTE,
        StageName.STRUCTURE,
        StageName.SUMMARIZE,
        StageName.CARD,
        StageName.OPENKB_ADD,
        StageName.INDEX,
        StageName.ACCEPTANCE,
        StageName.CLASSIFY,
    ):
        stage_status = (
            StageStatus.NOT_STARTED
            if downstream_has_source
            else StageStatus.SKIPPED
        )
        stage_outputs: list[str] = []
        stage_notes = (
            ["source-pack evidence available; stage not run in dry-run writer"]
            if source_pack_verified
            else ["waiting for source-pack evidence"]
            if has_pdf
            else ["skipped because no PDF handoff row exists"]
        )
        if stage_name == StageName.EXTRACT_NATIVE and native_extraction_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["native extraction evidence", "native markdown"]
            stage_notes = ["fixture native extraction evidence available"]
        elif stage_name == StageName.EXTRACT_OCR and ocr_extraction_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["OCR extraction evidence", "OCR markdown"]
            stage_notes = ["fixture OCR extraction evidence available"]
        elif stage_name == StageName.ROUTE and route_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["route selection evidence", "selected fulltext"]
            stage_notes = ["fixture route selection evidence available"]
        elif stage_name == StageName.STRUCTURE and structure_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["structure evidence"]
            if structure_outline_ready:
                stage_outputs.append("structure outline")
            stage_notes = ["fixture structure evidence available"]
        elif stage_name == StageName.SUMMARIZE and summarize_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["hierarchical summary", "summary texts"]
            stage_notes = ["fixture hierarchical summary available"]
        elif stage_name == StageName.CARD and card_ready:
            stage_status = StageStatus.PASSED
            stage_outputs = ["paper card json", "paper card markdown"]
            stage_notes = ["fixture paper card available"]
        stages.append(
            StageRecord(
                name=stage_name,
                status=stage_status,
                outputs=stage_outputs,
                notes=stage_notes,
            )
        )
    stages.append(
        StageRecord(
            name=StageName.WRITEBACK,
            status=StageStatus.SKIPPED,
            notes=["dry-run artifact writer does not write Zotero state"],
        )
    )
    return StageManifest(
        run_id=run_id,
        mode=RunMode.PREVIEW,
        manual_gates=manual_gates,
        stages=stages,
    )


def build_dry_run_artifact_index(
    *,
    item: DiscoveredItem,
    rows: list[OpenKBHandoffRow],
    run_id: str,
    run_dir: Path,
    stage_manifest: StageManifest,
    source_pack_ref: str | None = None,
    source_pack_manifest_ref: str | None = None,
    source_pack_hash: str | None = None,
    native_extraction_evidence_ref: str | None = None,
    native_extraction_markdown_ref: str | None = None,
    ocr_extraction_evidence_ref: str | None = None,
    ocr_extraction_markdown_ref: str | None = None,
    route_evidence_ref: str | None = None,
    route_markdown_ref: str | None = None,
    structure_evidence_ref: str | None = None,
    structure_outline_ref: str | None = None,
    summary_artifact_ref: str | None = None,
    summary_text_dir_ref: str | None = None,
    paper_card_json_ref: str | None = None,
    paper_card_markdown_ref: str | None = None,
) -> ArtifactIndex:
    paper_id = paper_id_for_item(item)
    source_pack = {
        "ref": source_pack_ref or f"source-packs/zotero/{paper_id}",
        "source_type": "zotero",
        "source_hash": source_pack_hash or _source_hash_for_rows(rows),
    }
    if source_pack_manifest_ref is not None:
        source_pack["manifest_ref"] = source_pack_manifest_ref
    artifacts = {
        "stage_manifest": {
            "kind": "stage-manifest",
            "ref": "stage-manifest.json",
            "format": "json",
            "stage": "discover",
            "private_content": False,
        }
    }
    if (
        native_extraction_evidence_ref is not None
        and native_extraction_markdown_ref is not None
    ):
        artifacts["native_extraction_evidence"] = {
            "kind": "native-extraction-evidence",
            "ref": native_extraction_evidence_ref,
            "format": "json",
            "stage": "extract-native",
            "private_content": False,
        }
        artifacts["native_extraction_markdown"] = {
            "kind": "native-extraction-markdown",
            "ref": native_extraction_markdown_ref,
            "format": "markdown",
            "stage": "extract-native",
            "private_content": True,
        }
    if (
        ocr_extraction_evidence_ref is not None
        and ocr_extraction_markdown_ref is not None
    ):
        artifacts["ocr_extraction_evidence"] = {
            "kind": "ocr-extraction-evidence",
            "ref": ocr_extraction_evidence_ref,
            "format": "json",
            "stage": "extract-ocr",
            "private_content": False,
        }
        artifacts["ocr_extraction_markdown"] = {
            "kind": "ocr-extraction-markdown",
            "ref": ocr_extraction_markdown_ref,
            "format": "markdown",
            "stage": "extract-ocr",
            "private_content": True,
        }
    if route_evidence_ref is not None and route_markdown_ref is not None:
        artifacts["route_evidence"] = {
            "kind": "route-selection-evidence",
            "ref": route_evidence_ref,
            "format": "json",
            "stage": "route",
            "private_content": False,
        }
        artifacts["selected_fulltext"] = {
            "kind": "selected-fulltext",
            "ref": route_markdown_ref,
            "format": "markdown",
            "stage": "route",
            "private_content": True,
        }
    if structure_evidence_ref is not None:
        artifacts["structure_evidence"] = {
            "kind": "structure-evidence",
            "ref": structure_evidence_ref,
            "format": "json",
            "stage": "structure",
            "private_content": False,
        }
        if structure_outline_ref is not None:
            artifacts["structure_outline"] = {
                "kind": "structure-outline",
                "ref": structure_outline_ref,
                "format": "markdown",
                "stage": "structure",
                "private_content": True,
            }
    if summary_artifact_ref is not None:
        artifacts["hierarchical_summary"] = {
            "kind": "hierarchical-summary",
            "ref": summary_artifact_ref,
            "format": "json",
            "stage": "summarize",
            "private_content": False,
        }
        if summary_text_dir_ref is not None:
            artifacts["summary_texts"] = {
                "kind": "summary-texts",
                "ref": summary_text_dir_ref,
                "format": "directory",
                "stage": "summarize",
                "private_content": True,
            }
    if paper_card_json_ref is not None:
        artifacts["paper_card_json"] = {
            "kind": "paper-card",
            "ref": paper_card_json_ref,
            "format": "json",
            "stage": "card",
            "private_content": False,
        }
        if paper_card_markdown_ref is not None:
            artifacts["paper_card_markdown"] = {
                "kind": "paper-card-markdown",
                "ref": paper_card_markdown_ref,
                "format": "markdown",
                "stage": "card",
                "private_content": True,
            }
    return ArtifactIndex(
        paper_id=paper_id,
        run_id=run_id,
        artifact_root=str(run_dir),
        source_pack=source_pack,
        source_identity=_source_identity_for_item(item, rows),
        stages={
            stage.name.value: {
                "status": stage.status.value,
                "manifest_ref": "stage-manifest.json",
            }
            for stage in stage_manifest.stages
        },
        artifacts=artifacts,
        indexes=[
            {
                "lane": "openkb",
                "status": "skipped",
                "skip_reason": "dry-run artifact writer does not write OpenKB",
            },
            {
                "lane": "pageindex",
                "status": "skipped",
                "skip_reason": "dry-run artifact writer does not write indexes",
            },
            {
                "lane": "condb",
                "status": "skipped",
                "skip_reason": "optional support lane not run",
            },
            {
                "lane": "chatindex",
                "status": "skipped",
                "skip_reason": "optional support lane not run",
            },
        ],
        zotero_writeback={
            "mode": "none",
            "status": "not-planned",
        },
    )


def _source_identity_for_item(
    item: DiscoveredItem, rows: list[OpenKBHandoffRow]
) -> dict[str, Any]:
    metadata = item.paper_metadata
    identity: dict[str, Any] = {
        "title": item.title,
        "year": metadata.year,
        "doi": metadata.doi,
        "citation_key": item.citation_key,
        "zotero_item_key": item.key,
        "pdf_count": len(rows),
    }
    if len(rows) == 1:
        identity["zotero_attachment_key"] = rows[0].attachment_key
        identity["canonical_filename"] = rows[0].canonical_filename
    return identity


def _source_hash_for_rows(rows: list[OpenKBHandoffRow]) -> str:
    hashes = sorted(row.sha256 for row in rows if row.sha256)
    if len(hashes) == 1:
        return f"sha256:{hashes[0]}"
    if len(hashes) > 1:
        digest = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
        return f"sha256-aggregate:{digest}"
    return "not-computed:dry-run"


def _relative_ref(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path, start=start)).as_posix()


def _resolve_extraction_refs(
    *,
    source_pack_dir: Path,
    run_dir: Path,
    expected_source_hash: str,
) -> dict[str, str | None]:
    native_evidence_ref, native_markdown_ref = _resolve_extraction_ref(
        evidence_path=source_pack_dir / NATIVE_EVIDENCE_REF,
        markdown_path=source_pack_dir / NATIVE_MARKDOWN_REF,
        run_dir=run_dir,
        expected_source_hash=expected_source_hash,
        loader=load_native_extraction_sidecar,
        stage="native extraction",
    )
    ocr_evidence_ref, ocr_markdown_ref = _resolve_extraction_ref(
        evidence_path=source_pack_dir / OCR_EVIDENCE_REF,
        markdown_path=source_pack_dir / OCR_MARKDOWN_REF,
        run_dir=run_dir,
        expected_source_hash=expected_source_hash,
        loader=load_ocr_extraction_sidecar,
        stage="OCR extraction",
    )
    route_evidence_ref, route_markdown_ref = _resolve_extraction_ref(
        evidence_path=source_pack_dir / ROUTE_EVIDENCE_REF,
        markdown_path=source_pack_dir / ROUTE_MARKDOWN_REF,
        run_dir=run_dir,
        expected_source_hash=expected_source_hash,
        loader=load_route_selection_sidecar,
        stage="route selection",
    )
    structure_evidence_ref, structure_outline_ref = _resolve_structure_ref(
        evidence_path=source_pack_dir / STRUCTURE_EVIDENCE_REF,
        outline_path=source_pack_dir / STRUCTURE_OUTLINE_REF,
        run_dir=run_dir,
        expected_source_hash=expected_source_hash,
    )
    return {
        "native_evidence_ref": native_evidence_ref,
        "native_markdown_ref": native_markdown_ref,
        "ocr_evidence_ref": ocr_evidence_ref,
        "ocr_markdown_ref": ocr_markdown_ref,
        "route_evidence_ref": route_evidence_ref,
        "route_markdown_ref": route_markdown_ref,
        "structure_evidence_ref": structure_evidence_ref,
        "structure_outline_ref": structure_outline_ref,
    }


def _resolve_extraction_ref(
    *,
    evidence_path: Path,
    markdown_path: Path,
    run_dir: Path,
    expected_source_hash: str,
    loader: Any,
    stage: str,
) -> tuple[str | None, str | None]:
    evidence_exists = evidence_path.exists()
    markdown_exists = markdown_path.exists()
    if not evidence_exists and not markdown_exists:
        return None, None
    if not evidence_path.is_file() or not markdown_path.is_file():
        raise ValueError(f"incomplete {stage} fixture under {evidence_path.parent}")
    payload = loader(evidence_path)
    if payload["source_hash"] != expected_source_hash:
        raise ValueError(
            f"{stage} evidence source_hash drift at {evidence_path}"
        )
    return (
        _relative_ref(evidence_path, run_dir),
        _relative_ref(markdown_path, run_dir),
    )


def _resolve_structure_ref(
    *,
    evidence_path: Path,
    outline_path: Path,
    run_dir: Path,
    expected_source_hash: str,
) -> tuple[str | None, str | None]:
    evidence_exists = evidence_path.exists()
    outline_exists = outline_path.exists()
    if not evidence_exists and not outline_exists:
        return None, None
    if not evidence_path.is_file():
        raise ValueError(
            f"incomplete structure fixture under {evidence_path.parent}"
        )
    if outline_exists and not outline_path.is_file():
        raise ValueError(
            f"incomplete structure fixture under {evidence_path.parent}"
        )
    payload = load_structure_sidecar(evidence_path)
    if payload["source_hash"] != expected_source_hash:
        raise ValueError(f"structure evidence source_hash drift at {evidence_path}")
    outline_ref = _relative_ref(outline_path, run_dir) if outline_exists else None
    return _relative_ref(evidence_path, run_dir), outline_ref


def _resolve_summary_refs(
    *,
    run_dir: Path,
    paper_id: str,
    run_id: str,
) -> dict[str, str | None]:
    summary_path = run_dir / SUMMARY_ARTIFACT_REF
    summary_text_dir = run_dir / SUMMARY_TEXT_DIR_REF
    summary_exists = summary_path.exists()
    text_dir_exists = summary_text_dir.exists()
    if not summary_exists and not text_dir_exists:
        return {
            "summary_artifact_ref": None,
            "summary_text_dir_ref": None,
        }
    if not summary_path.is_file() or not summary_text_dir.is_dir():
        raise ValueError(f"incomplete summary fixture under {summary_path.parent}")
    payload = load_hierarchical_summary(summary_path)
    if payload["paper_id"] != paper_id:
        raise ValueError(f"hierarchical summary paper_id drift at {summary_path}")
    if payload["run_id"] != run_id:
        raise ValueError(f"hierarchical summary run_id drift at {summary_path}")
    for summary in payload["summaries"]:
        text_ref = str(summary["text_ref"])
        text_path = summary_path.parent / text_ref
        if not text_path.is_file():
            raise ValueError(
                f"incomplete summary fixture under {summary_path.parent}"
            )
    return {
        "summary_artifact_ref": _relative_ref(summary_path, run_dir),
        "summary_text_dir_ref": _relative_ref(summary_text_dir, run_dir),
    }


def _resolve_card_refs(
    *,
    run_dir: Path,
    paper_id: str,
    expected_source_hash: str,
) -> dict[str, str | None]:
    card_json_path = run_dir / CARD_JSON_REF
    card_markdown_path = run_dir / CARD_MARKDOWN_REF
    json_exists = card_json_path.exists()
    markdown_exists = card_markdown_path.exists()
    if not json_exists and not markdown_exists:
        return {
            "paper_card_json_ref": None,
            "paper_card_markdown_ref": None,
        }
    if not card_json_path.is_file() or not card_markdown_path.is_file():
        raise ValueError(f"incomplete paper card fixture under {card_json_path.parent}")
    payload = load_paper_card(card_json_path)
    if payload["paper_id"] != paper_id:
        raise ValueError(f"paper card paper_id drift at {card_json_path}")
    identity = payload["identity"]
    if identity.get("source_hash") not in (None, expected_source_hash):
        raise ValueError(f"paper card source_hash drift at {card_json_path}")
    return {
        "paper_card_json_ref": _relative_ref(card_json_path, run_dir),
        "paper_card_markdown_ref": _relative_ref(card_markdown_path, run_dir),
    }
