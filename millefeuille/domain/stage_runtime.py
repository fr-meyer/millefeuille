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
_SOURCE_PACK_ARTIFACT_ROOT = "source-pack"


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
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
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
    canonical_run_dir = (
        source_pack_dir
        / "analyses"
        / "millefeuille"
        / resolved_run_id
    )
    run_dir, stage_manifest_path = _resolve_run_package_paths(
        canonical_run_dir=canonical_run_dir,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
        paper_id=resolved_paper_id,
        run_id=resolved_run_id,
    )
    artifact_index_path = run_dir / "artifact-index.json"

    if artifact_reader is None:
        ensure_no_follow_regular_file(
            manifest_path,
            "source-pack manifest",
            root=root,
        )
        ensure_no_follow_regular_file(
            stage_manifest_path,
            "stage manifest",
            root=run_dir,
        )
        ensure_no_follow_regular_file(
            artifact_index_path,
            "artifact index",
            root=run_dir,
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
        run_dir=run_dir,
        source_pack_manifest=source_pack_manifest,
        stage_manifest=stage_manifest,
        stage_manifest_path=stage_manifest_path,
        artifact_index=artifact_index,
        source_pack_dir=source_pack_dir,
    )
    return ResolvedRunArtifacts(
        paper_id=resolved_paper_id,
        run_id=resolved_run_id,
        source_pack_root=root,
        source_pack_dir=source_pack_dir,
        run_dir=run_dir,
        source_hash=str(source_pack_manifest["source_hash"]),
        source_pack_manifest=source_pack_manifest,
        stage_manifest_path=stage_manifest_path,
        artifact_index_path=artifact_index_path,
        stage_manifest=stage_manifest,
        artifact_index=artifact_index,
    )


def _resolve_run_package_paths(
    *,
    canonical_run_dir: Path,
    artifact_root: str | Path | None,
    stage_manifest: str | Path | None,
    paper_id: str,
    run_id: str,
) -> tuple[Path, Path]:
    """Resolve exactly one run package without following indirection.

    An artifact root is a locator, not an identity override. It may be the run
    directory itself or one of the declared container layouts. A broad root
    that contains more than one matching package fails closed instead of
    relying on search order.
    """

    artifact_root_value = (
        os.fspath(artifact_root) if artifact_root is not None else None
    )
    source_pack_selected = artifact_root_value == _SOURCE_PACK_ARTIFACT_ROOT
    explicit_root: Path | None
    if artifact_root is None or source_pack_selected:
        explicit_root = None
    else:
        explicit_root = _require_safe_override_path(artifact_root, "artifact root")
        ensure_no_follow_directory(explicit_root, "artifact root")

    explicit_manifest = (
        _require_safe_override_path(stage_manifest, "stage manifest")
        if stage_manifest is not None
        else None
    )
    if explicit_manifest is not None:
        ensure_no_follow_regular_file(explicit_manifest, "stage manifest")
        run_dir = explicit_manifest.parent
        ensure_no_follow_directory(run_dir, "artifact run directory")
        if source_pack_selected and not _same_path(run_dir, canonical_run_dir):
            raise MillefeuilleContractError(
                "stage manifest does not belong to the selected source-pack run"
            )
        if explicit_root is not None:
            expected_dirs = _artifact_run_candidates(
                explicit_root,
                paper_id=paper_id,
                run_id=run_id,
            )
            if not any(_same_path(run_dir, candidate) for candidate in expected_dirs):
                raise MillefeuilleContractError(
                    "stage manifest does not belong to the selected artifact root, "
                    "paper_id, and run_id"
                )
        return run_dir, explicit_manifest

    if explicit_root is None:
        return canonical_run_dir, canonical_run_dir / "stage-manifest.json"

    matches: list[Path] = []
    for candidate in _artifact_run_candidates(
        explicit_root,
        paper_id=paper_id,
        run_id=run_id,
    ):
        manifest_path = candidate / "stage-manifest.json"
        index_path = candidate / "artifact-index.json"
        if probe_no_follow_regular_file(
            manifest_path,
            "stage manifest",
            root=explicit_root,
        ) and probe_no_follow_regular_file(
            index_path,
            "artifact index",
            root=explicit_root,
        ):
            matches.append(candidate)

    if not matches:
        raise MillefeuilleContractError(
            "artifact root contains no complete run package for the requested "
            "paper_id and run_id"
        )
    if len(matches) != 1:
        raise MillefeuilleContractError(
            "artifact root is ambiguous: multiple complete run packages match "
            "the requested paper_id and run_id"
        )
    return matches[0], matches[0] / "stage-manifest.json"


def _artifact_run_candidates(
    root: Path,
    *,
    paper_id: str,
    run_id: str,
) -> list[Path]:
    candidates = (
        root,
        root / run_id,
        root / paper_id / run_id,
        root / "analyses" / "millefeuille" / run_id,
        root / paper_id / "analyses" / "millefeuille" / run_id,
        root / "zotero" / paper_id / "analyses" / "millefeuille" / run_id,
    )
    result: list[Path] = []
    for candidate in candidates:
        if not any(_same_path(candidate, existing) for existing in result):
            result.append(candidate)
    return result


def _require_safe_override_path(value: str | Path, label: str) -> Path:
    text = os.fspath(value)
    if not isinstance(text, str) or not text or text != text.strip() or "\x00" in text:
        raise MillefeuilleContractError(
            f"{label} must be a non-empty path without surrounding whitespace"
        )
    path = Path(text)
    if ".." in path.parts:
        raise MillefeuilleContractError(
            f"{label} path must not contain parent traversal"
        )
    return path


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
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


def _is_path_indirection(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(file_attributes & reparse_flag)


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
                f"{label} escapes its trusted root: {target}"
            ) from exc
        current = boundary
        boundary_stat = _lstat_path(boundary, label)
        if _is_path_indirection(boundary_stat):
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or reparse points"
            )
        if not stat.S_ISDIR(boundary_stat.st_mode):
            raise MillefeuilleContractError(
                f"{label} path boundary is not a directory: {boundary}"
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
        if _is_path_indirection(current_stat):
            if index == len(parts) - 1:
                raise MillefeuilleContractError(
                    f"{label} must not be a symbolic link or reparse point"
                )
            raise MillefeuilleContractError(
                f"{label} path must not contain symbolic links or reparse points"
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
    final_stat = _walk_no_follow(
        target,
        label=label,
        root=Path(root) if root is not None else None,
    )
    if final_stat is None or not stat.S_ISREG(final_stat.st_mode):
        raise MillefeuilleContractError(f"{label} is not a regular file: {target}")
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
    stage_manifest_path: Path,
    artifact_index: ArtifactIndex,
    source_pack_dir: Path,
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

    if not _same_path(artifact_index.artifact_root, run_dir):
        raise MillefeuilleContractError(
            "artifact index artifact_root drift: indexed root does not match "
            "the selected run package"
        )

    indexed_source_ref = artifact_index.source_pack.get("ref")
    if not isinstance(indexed_source_ref, str) or not indexed_source_ref.strip():
        raise MillefeuilleContractError("artifact index source-pack ref is missing")
    indexed_source_path = Path(indexed_source_ref)
    indexed_source_candidates = [indexed_source_path]
    if not indexed_source_path.is_absolute():
        indexed_source_candidates.append(run_dir / indexed_source_path)
    if not any(
        _same_path(candidate, source_pack_dir)
        for candidate in indexed_source_candidates
    ):
        raise MillefeuilleContractError(
            "artifact index source-pack ref drift: indexed ref does not match "
            "the selected source pack"
        )

    indexed_manifest_ref = artifact_index.source_pack.get("manifest_ref")
    if indexed_manifest_ref is not None:
        if (
            not isinstance(indexed_manifest_ref, str)
            or not indexed_manifest_ref.strip()
        ):
            raise MillefeuilleContractError(
                "artifact index source-pack manifest_ref must be a non-empty string"
            )
        indexed_manifest_path = Path(indexed_manifest_ref)
        indexed_manifest_candidates = [indexed_manifest_path]
        if not indexed_manifest_path.is_absolute():
            indexed_manifest_candidates.append(run_dir / indexed_manifest_path)
        if not any(
            _same_path(candidate, source_pack_dir / "manifest.json")
            for candidate in indexed_manifest_candidates
        ):
            raise MillefeuilleContractError(
                "artifact index source-pack manifest_ref drift"
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
        expected_manifest_ref = relative_ref(stage_manifest_path, run_dir)
        if record.get("manifest_ref") != expected_manifest_ref:
            raise MillefeuilleContractError(
                f"stage manifest ref drift for {stage_name!r}"
            )

    stage_manifest_artifact = artifact_index.artifacts.get("stage_manifest")
    if stage_manifest_artifact is not None and stage_manifest_artifact.get(
        "ref"
    ) != relative_ref(stage_manifest_path, run_dir):
        raise MillefeuilleContractError("stage manifest artifact ref drift")

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
    existing_stage = payload["stages"].get(stage_name.value, {})
    payload["stages"][stage_name.value] = {
        "status": stage_status,
        "manifest_ref": existing_stage.get("manifest_ref", "stage-manifest.json"),
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
