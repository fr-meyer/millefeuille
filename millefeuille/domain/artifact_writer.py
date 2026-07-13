"""Artifact writer helpers for dry-run Millefeuille evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.artifacts import ArtifactIndex, write_artifact_index
from millefeuille.domain.config import ArtifactExportConfig
from millefeuille.domain.millefeuille import (
    ManualGate,
    RunMode,
    StageManifest,
    StageName,
    StageRecord,
    StageStatus,
)
from millefeuille.domain.models import DiscoveredItem, OpenKBHandoffRow


@dataclass(frozen=True)
class ArtifactWriteResult:
    paper_id: str
    run_id: str
    run_dir: Path
    artifact_index_path: Path
    stage_manifest_path: Path


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
    if str(config.artifact_root).strip() == "source-pack":
        raise ValueError(
            "export.artifacts.artifact_root=source-pack requires a later "
            "source-pack writer; use an explicit filesystem path for this dry-run "
            "artifact slice"
        )

    root = Path(config.artifact_root)
    run_id = str(config.run_id).strip() if config.run_id else default_artifact_run_id()
    rows_by_item: dict[str, list[OpenKBHandoffRow]] = {}
    for row in handoff_rows:
        rows_by_item.setdefault(row.item_key, []).append(row)

    results: list[ArtifactWriteResult] = []
    for item in items:
        paper_id = paper_id_for_item(item)
        item_rows = rows_by_item.get(item.key, [])
        run_dir = root / paper_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        stage_manifest = build_dry_run_stage_manifest(
            item=item,
            rows=item_rows,
            run_id=run_id,
            handoff_enabled=handoff_enabled,
        )
        stage_manifest_path = run_dir / "stage-manifest.json"
        write_stage_manifest(stage_manifest, stage_manifest_path)

        artifact_index = build_dry_run_artifact_index(
            item=item,
            rows=item_rows,
            run_id=run_id,
            run_dir=run_dir,
            stage_manifest=stage_manifest,
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


def paper_id_for_item(item: DiscoveredItem) -> str:
    raw = f"zotero-{item.key}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    return slug or "zotero-item"


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
) -> StageManifest:
    has_pdf = bool(rows)
    manual_gates = (
        [ManualGate.PDF_RECOVERY, ManualGate.SOURCE_PACK_WRITE]
        if has_pdf
        else []
    )
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
            status=StageStatus.MANUAL_GATE if has_pdf else StageStatus.SKIPPED,
            inputs=["openkb-millefeuille-handoff-preview"] if has_pdf else [],
            manual_gate_required=has_pdf,
            gate=ManualGate.PDF_RECOVERY if has_pdf else None,
            notes=["PDF recovery not performed by dry-run artifact writer"]
            if has_pdf
            else ["no PDF rows to recover"],
        ),
        StageRecord(
            name=StageName.SOURCE_PACK,
            status=StageStatus.MANUAL_GATE if has_pdf else StageStatus.SKIPPED,
            inputs=["verified recovered PDF bytes"] if has_pdf else [],
            manual_gate_required=has_pdf,
            gate=ManualGate.SOURCE_PACK_WRITE if has_pdf else None,
            notes=["source-pack creation intentionally not performed"]
            if has_pdf
            else ["no source-pack planned without a PDF row"],
        ),
    ]
    stages.extend(
        StageRecord(
            name=stage_name,
            status=StageStatus.NOT_STARTED if has_pdf else StageStatus.SKIPPED,
            notes=["waiting for source-pack evidence"]
            if has_pdf
            else ["skipped because no PDF handoff row exists"],
        )
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
) -> ArtifactIndex:
    paper_id = paper_id_for_item(item)
    return ArtifactIndex(
        paper_id=paper_id,
        run_id=run_id,
        artifact_root=str(run_dir),
        source_pack={
            "ref": f"source-packs/zotero/{paper_id}",
            "source_type": "zotero",
            "source_hash": _source_hash_for_rows(rows),
        },
        source_identity=_source_identity_for_item(item, rows),
        stages={
            stage.name.value: {
                "status": stage.status.value,
                "manifest_ref": "stage-manifest.json",
            }
            for stage in stage_manifest.stages
        },
        artifacts={
            "stage_manifest": {
                "kind": "stage-manifest",
                "ref": "stage-manifest.json",
                "format": "json",
                "stage": "discover",
                "private_content": False,
            }
        },
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
