"""Shared helpers for offline Millefeuille stage commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
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
from millefeuille.domain.secure_io import (
    RootArtifactReader,
    load_json_object_no_follow,
    read_text_no_follow,
    verify_regular_file_no_follow,
)
from millefeuille.domain.source_packs import (
    load_source_pack_manifest,
    paper_id_for_zotero_item_key,
    parse_source_pack_manifest,
)

_SAFE_PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


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
    artifact_reader: RootArtifactReader | None = None,
) -> ResolvedRunArtifacts:
    requested_item_key: str | None = None
    if item_key is not None:
        requested_item_key = str(item_key).strip()
        if not requested_item_key:
            raise MillefeuilleContractError("item_key must not be empty")
    resolved_paper_id = paper_id or (
        paper_id_for_zotero_item_key(requested_item_key)
        if requested_item_key
        else None
    )
    if not isinstance(resolved_paper_id, str) or not resolved_paper_id.strip():
        raise MillefeuilleContractError("paper_id or item_key is required")
    resolved_paper_id = resolved_paper_id.strip()
    require_safe_package_id(resolved_paper_id, "paper_id")

    resolved_run_id = str(run_id).strip()
    if not resolved_run_id:
        raise MillefeuilleContractError("run_id must not be empty")
    require_safe_package_id(resolved_run_id, "run_id")

    root = Path(source_pack_root)
    ensure_no_follow_directory(root, "source-pack root")
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

    if artifact_reader is None:
        ensure_no_follow_regular_file(
            manifest_path,
            "source-pack manifest",
            root=root,
        )
        ensure_no_follow_regular_file(
            stage_manifest_path,
            "stage manifest",
            root=root,
        )
        ensure_no_follow_regular_file(
            artifact_index_path,
            "artifact index",
            root=root,
        )
        source_pack_manifest = load_source_pack_manifest(manifest_path)
        stage_manifest = load_stage_manifest(stage_manifest_path)
        artifact_index = load_artifact_index(artifact_index_path)
    else:
        source_pack_manifest = parse_source_pack_manifest(
            artifact_reader.load_json_object(
                manifest_path,
                "source-pack manifest",
            )
        )
        stage_manifest = StageManifest.from_dict(
            artifact_reader.load_json_object(stage_manifest_path, "stage manifest")
        )
        artifact_index = ArtifactIndex.from_dict(
            artifact_reader.load_json_object(artifact_index_path, "artifact index")
        )
    if requested_item_key is not None:
        manifest_identity = source_pack_manifest.get("identity")
        actual_item_key = (
            manifest_identity.get("zotero_item_key")
            if isinstance(manifest_identity, dict)
            else None
        )
        if actual_item_key != requested_item_key:
            raise MillefeuilleContractError(
                "source-pack item_key drift: "
                f"expected {requested_item_key!r}, got {actual_item_key!r}"
            )
    _validate_run_identity(
        paper_id=resolved_paper_id,
        run_id=resolved_run_id,
        run_dir=artifact_index_path.parent,
        source_pack_manifest=source_pack_manifest,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )
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


def require_safe_package_id(value: str, field_name: str) -> None:
    if value in {".", ".."} or _SAFE_PACKAGE_ID.fullmatch(value) is None:
        raise MillefeuilleContractError(
            f"{field_name} must be traversal-safe and use only letters, numbers, "
            "dots, underscores, and hyphens"
        )


def _lstat_path(path: Path, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(f"{label} not found: {path}") from exc
    except OSError as exc:
        raise MillefeuilleContractError(
            f"could not inspect {label} {path}: {exc}"
        ) from exc


def _walk_no_follow(
    path: Path,
    *,
    label: str,
    root: Path | None = None,
    allow_missing: bool = False,
) -> os.stat_result | None:
    target = Path(path)
    if root is not None:
        boundary = Path(root)
        try:
            relative = target.relative_to(boundary)
        except ValueError as exc:
            raise MillefeuilleContractError(
                f"{label} escapes the source-pack root: {target}"
            ) from exc
        current = boundary
        boundary_stat = _lstat_path(boundary, "source-pack root")
        if stat.S_ISLNK(boundary_stat.st_mode):
            raise MillefeuilleContractError(
                f"source-pack root must not be a symbolic link: {boundary}"
            )
        if not stat.S_ISDIR(boundary_stat.st_mode):
            raise MillefeuilleContractError(
                f"source-pack root is not a directory: {boundary}"
            )
        parts = relative.parts
        final_stat: os.stat_result | None = boundary_stat if not parts else None
    else:
        current = Path(target.anchor) if target.is_absolute() else Path(".")
        parts = target.parts[1:] if target.is_absolute() else target.parts
        final_stat = _lstat_path(current, label) if not parts else None
    for index, part in enumerate(parts):
        current = current / part
        try:
            current_stat = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return None
            raise MillefeuilleContractError(f"{label} not found: {current}") from None
        except OSError as exc:
            raise MillefeuilleContractError(
                f"could not inspect {label} {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(current_stat.st_mode):
            if index == len(parts) - 1:
                raise MillefeuilleContractError(
                    f"{label} must not be a symbolic link"
                )
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links"
            )
        if index != len(parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
            raise MillefeuilleContractError(
                f"{label} path parent is not a directory: {current}"
            )
        final_stat = current_stat
    return final_stat


def ensure_no_follow_regular_file(
    path: str | Path,
    label: str,
    *,
    root: str | Path | None = None,
) -> None:
    target = Path(path)
    if root is not None:
        boundary = Path(root)
        try:
            target.relative_to(boundary)
        except ValueError as exc:
            raise MillefeuilleContractError(
                f"{label} escapes the source-pack root: {target}"
            ) from exc
    verify_regular_file_no_follow(target, label)


def ensure_no_follow_directory(
    path: str | Path,
    label: str,
    *,
    root: str | Path | None = None,
) -> None:
    target = Path(path)
    final_stat = _walk_no_follow(
        target,
        label=label,
        root=Path(root) if root is not None else None,
    )
    if final_stat is None or not stat.S_ISDIR(final_stat.st_mode):
        raise MillefeuilleContractError(f"{label} is not a directory: {target}")


def probe_no_follow_regular_file(
    path: str | Path,
    label: str,
    *,
    root: str | Path | None = None,
) -> bool:
    target = Path(path)
    final_stat = _walk_no_follow(
        target,
        label=label,
        root=Path(root) if root is not None else None,
        allow_missing=True,
    )
    return final_stat is not None and stat.S_ISREG(final_stat.st_mode)


def _validate_run_identity(
    *,
    paper_id: str,
    run_id: str,
    run_dir: Path,
    source_pack_manifest: dict[str, Any],
    stage_manifest: StageManifest,
    artifact_index: ArtifactIndex,
) -> None:
    """Reject cross-wired run metadata before any derived artifact is written."""

    if source_pack_manifest.get("paper_id") != paper_id:
        raise MillefeuilleContractError(
            "source-pack manifest paper_id drift: "
            f"expected {paper_id!r}, got {source_pack_manifest.get('paper_id')!r}"
        )
    if stage_manifest.run_id != run_id:
        raise MillefeuilleContractError(
            "stage manifest run_id drift: "
            f"expected {run_id!r}, got {stage_manifest.run_id!r}"
        )
    if artifact_index.paper_id != paper_id:
        raise MillefeuilleContractError(
            "artifact index paper_id drift: "
            f"expected {paper_id!r}, got {artifact_index.paper_id!r}"
        )
    if artifact_index.run_id != run_id:
        raise MillefeuilleContractError(
            "artifact index run_id drift: "
            f"expected {run_id!r}, got {artifact_index.run_id!r}"
        )

    source_hash = str(source_pack_manifest.get("source_hash", ""))
    indexed_source_hash = str(artifact_index.source_pack.get("source_hash", ""))
    if indexed_source_hash != source_hash:
        raise MillefeuilleContractError(
            "artifact index source_hash drift: "
            f"expected {source_hash!r}, got {indexed_source_hash!r}"
        )

    source_type = str(source_pack_manifest.get("source_type", ""))
    indexed_source_type = str(artifact_index.source_pack.get("source_type", ""))
    if indexed_source_type != source_type:
        raise MillefeuilleContractError(
            "artifact index source_type drift: "
            f"expected {source_type!r}, got {indexed_source_type!r}"
        )

    manifest_identity = source_pack_manifest.get("identity")
    if isinstance(manifest_identity, dict):
        for field_name in (
            "zotero_item_key",
            "zotero_attachment_key",
            "canonical_filename",
        ):
            expected = manifest_identity.get(field_name)
            indexed = artifact_index.source_identity.get(field_name)
            if expected is not None and indexed != expected:
                raise MillefeuilleContractError(
                    f"artifact index {field_name} drift: "
                    f"expected {expected!r}, got {indexed!r}"
                )

    manifest_statuses = stage_status_map(stage_manifest)
    manifest_stage_names = set(manifest_statuses)
    indexed_stage_names = set(artifact_index.stages)
    if indexed_stage_names != manifest_stage_names:
        missing = sorted(manifest_stage_names - indexed_stage_names)
        unexpected = sorted(indexed_stage_names - manifest_stage_names)
        raise MillefeuilleContractError(
            "stage set drift between manifest and artifact index: "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
    for stage_name, record in artifact_index.stages.items():
        manifest_status = manifest_statuses.get(stage_name)
        indexed_status = str(record.get("status", ""))
        if indexed_status != manifest_status:
            raise MillefeuilleContractError(
                f"stage status drift for {stage_name!r}: "
                f"manifest={manifest_status!r}, index={indexed_status!r}"
            )

    if not run_dir.is_dir():  # defensive: callers resolve an existing run directory
        raise MillefeuilleContractError(f"run directory is missing: {run_dir}")


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
    return load_json_object_no_follow(path, label)


def load_jsonl_records(path: str | Path, label: str) -> list[dict[str, Any]]:
    target = Path(path)
    text = read_text_no_follow(target, label)

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
