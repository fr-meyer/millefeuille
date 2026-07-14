"""Shared helpers for offline Millefeuille stage commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from millefeuille.domain.artifacts import (
    ArtifactIndex,
    load_artifact_index,
    write_artifact_index,
)
from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    StageManifest,
    StageName,
    StageRecord,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
)


@dataclass(frozen=True)
class ResolvedRunArtifacts:
    paper_id: str
    run_id: str
    source_pack_root: Path
    source_pack_dir: Path
    run_dir: Path
    source_hash: str
    source_pack_manifest: dict[str, Any]
    stage_manifest_path: Path
    artifact_index_path: Path
    stage_manifest: StageManifest
    artifact_index: ArtifactIndex


def resolve_run_artifacts(
    *,
    source_pack_root: str | Path,
    run_id: str,
    paper_id: str | None = None,
    item_key: str | None = None,
) -> ResolvedRunArtifacts:
    resolved_paper_id = paper_id or (
        paper_id_for_zotero_item_key(item_key) if item_key else None
    )
    if not isinstance(resolved_paper_id, str) or not resolved_paper_id.strip():
        raise MillefeuilleContractError("paper_id or item_key is required")

    resolved_run_id = str(run_id).strip()
    if not resolved_run_id:
        raise MillefeuilleContractError("run_id must not be empty")

    root = Path(source_pack_root)
    source_pack_dir = root / "zotero" / resolved_paper_id
    manifest_path = source_pack_dir / "manifest.json"
    stage_manifest_path = (
        source_pack_dir
        / "analyses"
        / "millefeuille"
        / resolved_run_id
        / "stage-manifest.json"
    )
    artifact_index_path = (
        source_pack_dir
        / "analyses"
        / "millefeuille"
        / resolved_run_id
        / "artifact-index.json"
    )
    if not stage_manifest_path.is_file():
        raise MillefeuilleContractError(
            f"stage manifest not found: {stage_manifest_path}"
        )
    if not artifact_index_path.is_file():
        raise MillefeuilleContractError(
            f"artifact index not found: {artifact_index_path}"
        )

    source_pack_manifest = load_source_pack_manifest(manifest_path)
    stage_manifest = load_stage_manifest(stage_manifest_path)
    artifact_index = load_artifact_index(artifact_index_path)
    return ResolvedRunArtifacts(
        paper_id=resolved_paper_id,
        run_id=resolved_run_id,
        source_pack_root=root,
        source_pack_dir=source_pack_dir,
        run_dir=artifact_index_path.parent,
        source_hash=str(source_pack_manifest["source_hash"]),
        source_pack_manifest=source_pack_manifest,
        stage_manifest_path=stage_manifest_path,
        artifact_index_path=artifact_index_path,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )


def load_stage_manifest(path: str | Path) -> StageManifest:
    manifest_path = Path(path)
    payload = load_json_object(manifest_path, "stage manifest")
    return StageManifest.from_dict(payload)


def write_stage_manifest(manifest: StageManifest, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {label} {target}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MillefeuilleContractError(f"{label} is not valid JSON: {target}") from exc
    if not isinstance(payload, dict):
        raise MillefeuilleContractError(f"{label} must be an object")
    return payload


def load_jsonl_records(path: str | Path, label: str) -> list[dict[str, Any]]:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not read {label} {target}: {exc}"
        ) from exc

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MillefeuilleContractError(
                f"{label} is not valid JSONL at {target}:{line_number}"
            ) from exc
        if not isinstance(payload, dict):
            raise MillefeuilleContractError(
                f"{label} record at {target}:{line_number} must be an object"
            )
        records.append(payload)
    return records


def write_json_object(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    output_path.write_text(
        ("\n".join(lines) + "\n") if lines else "",
        encoding="utf-8",
    )


def write_text(path: str | Path, text: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def relative_ref(path: str | Path, start: str | Path) -> str:
    return Path(os.path.relpath(Path(path), start=Path(start))).as_posix()


def upsert_stage_record(
    manifest: StageManifest,
    *,
    name: StageName,
    status: str,
    outputs: list[str] | None = None,
    notes: list[str] | None = None,
    inputs: list[str] | None = None,
) -> StageManifest:
    updated: list[StageRecord] = []
    replaced = False
    for stage in manifest.stages:
        if stage.name == name:
            updated.append(
                StageRecord(
                    name=stage.name,
                    status=status,
                    inputs=list(stage.inputs if inputs is None else inputs),
                    outputs=list(stage.outputs if outputs is None else outputs),
                    manual_gate_required=False,
                    gate=None,
                    notes=list(stage.notes if notes is None else notes),
                )
            )
            replaced = True
            continue
        updated.append(stage)
    if not replaced:
        updated.append(
            StageRecord(
                name=name,
                status=status,
                inputs=list(inputs or []),
                outputs=list(outputs or []),
                notes=list(notes or []),
            )
        )
    return StageManifest(
        run_id=manifest.run_id,
        mode=manifest.mode,
        stages=updated,
        manual_gates=[gate.value for gate in manifest.manual_gates],
        schema_version=manifest.schema_version,
        source_type=manifest.source_type,
    )


def stage_status_map(manifest: StageManifest) -> dict[str, str]:
    return {stage.name.value: stage.status.value for stage in manifest.stages}


def upsert_artifact_record(
    artifact_index: ArtifactIndex,
    *,
    name: str,
    kind: str,
    ref: str,
    format: str,
    stage: str,
    private_content: bool,
) -> ArtifactIndex:
    artifacts = artifact_index.to_dict()["artifacts"]
    artifacts[name] = {
        "kind": kind,
        "ref": ref,
        "format": format,
        "stage": stage,
        "private_content": private_content,
    }
    payload = artifact_index.to_dict()
    payload["artifacts"] = artifacts
    return ArtifactIndex.from_dict(payload)


def update_artifact_index(
    artifact_index: ArtifactIndex,
    *,
    stage_name: StageName,
    stage_status: str,
    zotero_writeback: dict[str, Any] | None = None,
) -> ArtifactIndex:
    payload = artifact_index.to_dict()
    payload["stages"][stage_name.value] = {
        "status": stage_status,
        "manifest_ref": "stage-manifest.json",
    }
    if zotero_writeback is not None:
        payload["zotero_writeback"] = dict(zotero_writeback)
    return ArtifactIndex.from_dict(payload)


def persist_run_artifacts(
    resolved: ResolvedRunArtifacts,
    *,
    stage_manifest: StageManifest,
    artifact_index: ArtifactIndex,
) -> None:
    write_stage_manifest(stage_manifest, resolved.stage_manifest_path)
    write_artifact_index(artifact_index, resolved.artifact_index_path)
