"""Artifact index helpers for read-only Millefeuille status commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.millefeuille import (
    MillefeuilleContractError,
    StageStatus,
)
from millefeuille.domain.secure_io import load_json_object_no_follow

ARTIFACT_INDEX_SCHEMA_VERSION = "millefeuille-artifact-index/v0.1"

SOURCE_TYPES = frozenset({"zotero", "fixture", "other"})
INDEX_LANES = frozenset({"openkb", "pageindex", "condb", "chatindex", "other"})
INDEX_STATUSES = frozenset(
    {
        "skipped",
        "previewed",
        "written",
        "failed",
        "needs-review",
        "not-started",
    }
)
WRITEBACK_MODES = frozenset({"none", "preview", "approved-live"})
WRITEBACK_INCOMPLETE_STATUSES = frozenset(
    {
        "failed",
        "manual-gate",
        "needs-review",
        "not-started",
    }
)
WRITEBACK_STATUSES = (
    frozenset({"not-planned", "previewed", "written", "skipped"})
    | WRITEBACK_INCOMPLETE_STATUSES
)

BLOCKING_STAGE_STATUSES = frozenset(
    {
        StageStatus.NOT_STARTED.value,
        StageStatus.FAILED.value,
        StageStatus.NEEDS_REVIEW.value,
        StageStatus.MANUAL_GATE.value,
    }
)
BLOCKING_INDEX_STATUSES = frozenset({"failed", "needs-review", "not-started"})
BLOCKING_WRITEBACK_STATUSES = WRITEBACK_INCOMPLETE_STATUSES


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MillefeuilleContractError(f"{field_name} must be an object")
    return value


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MillefeuilleContractError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ArtifactIndexStatus:
    complete: bool
    blocking_items: list[str]
    stage_counts: dict[str, int]
    index_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "blocking_items": list(self.blocking_items),
            "stage_counts": dict(self.stage_counts),
            "index_counts": dict(self.index_counts),
        }


@dataclass(frozen=True)
class ArtifactIndex:
    paper_id: str
    run_id: str
    artifact_root: str
    source_pack: dict[str, Any]
    source_identity: dict[str, Any]
    stages: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    indexes: list[dict[str, Any]]
    zotero_writeback: dict[str, Any]
    schema_version: str = ARTIFACT_INDEX_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ArtifactIndex:
        mapping = _require_mapping(payload, "artifact index")
        schema_version = mapping.get("schema_version")
        if schema_version != ARTIFACT_INDEX_SCHEMA_VERSION:
            raise MillefeuilleContractError(
                f"unsupported artifact index schema_version {schema_version!r}"
            )

        paper_id = _require_string(mapping.get("paper_id"), "paper_id")
        run_id = _require_string(mapping.get("run_id"), "run_id")
        artifact_root = _require_string(mapping.get("artifact_root"), "artifact_root")
        source_pack = _validate_source_pack(mapping.get("source_pack"))
        source_identity = _validate_source_identity(mapping.get("source_identity"))
        stages = _validate_stages(mapping.get("stages"))
        artifacts = _validate_artifacts(mapping.get("artifacts"))
        indexes = _validate_indexes(mapping.get("indexes"))
        zotero_writeback = _validate_writeback(mapping.get("zotero_writeback"))

        return cls(
            paper_id=paper_id,
            run_id=run_id,
            artifact_root=artifact_root,
            source_pack=source_pack,
            source_identity=source_identity,
            stages=stages,
            artifacts=artifacts,
            indexes=indexes,
            zotero_writeback=zotero_writeback,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "artifact_root": self.artifact_root,
            "source_pack": dict(self.source_pack),
            "source_identity": dict(self.source_identity),
            "stages": {name: dict(record) for name, record in self.stages.items()},
            "artifacts": {
                name: dict(record) for name, record in self.artifacts.items()
            },
            "indexes": [dict(record) for record in self.indexes],
            "zotero_writeback": dict(self.zotero_writeback),
        }

    def status(self) -> ArtifactIndexStatus:
        blocking_items: list[str] = []
        stage_counts: dict[str, int] = {}
        index_counts: dict[str, int] = {}

        for stage_name, record in sorted(self.stages.items()):
            status = str(record["status"])
            stage_counts[status] = stage_counts.get(status, 0) + 1
            if status in BLOCKING_STAGE_STATUSES:
                blocking_items.append(f"stage {stage_name}: {status}")

        for index_record in self.indexes:
            lane = str(index_record["lane"])
            status = str(index_record["status"])
            index_counts[status] = index_counts.get(status, 0) + 1
            if status in BLOCKING_INDEX_STATUSES:
                blocking_items.append(f"index {lane}: {status}")

        writeback_mode = str(self.zotero_writeback["mode"])
        writeback_status = str(self.zotero_writeback["status"])
        if (
            writeback_status in BLOCKING_WRITEBACK_STATUSES
            or writeback_mode == "approved-live"
            and writeback_status != "written"
        ):
            blocking_items.append(
                f"zotero_writeback {writeback_mode}: {writeback_status}"
            )

        return ArtifactIndexStatus(
            complete=not blocking_items,
            blocking_items=blocking_items,
            stage_counts=stage_counts,
            index_counts=index_counts,
        )


def load_artifact_index(path: str | Path) -> ArtifactIndex:
    payload = load_json_object_no_follow(path, "artifact index")
    return ArtifactIndex.from_dict(payload)


def write_artifact_index(index: ArtifactIndex, path: str | Path) -> None:
    index_path = Path(path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resolve_artifact_index_path(
    *,
    index: str | Path | None = None,
    artifact_root: str | Path | None = None,
    paper_id: str | None = None,
    run_id: str | None = None,
) -> Path:
    if index is not None:
        return Path(index)
    if artifact_root is None:
        raise MillefeuilleContractError(
            "either --index or --artifact-root must be provided"
        )

    root = Path(artifact_root)
    if root.is_file():
        return root

    candidates = [root / "artifact-index.json"]
    if run_id:
        candidates.append(root / run_id / "artifact-index.json")
        candidates.append(
            root / "analyses" / "millefeuille" / run_id / "artifact-index.json"
        )
    if paper_id and run_id:
        candidates.append(root / paper_id / run_id / "artifact-index.json")
        candidates.append(
            root
            / paper_id
            / "analyses"
            / "millefeuille"
            / run_id
            / "artifact-index.json"
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise MillefeuilleContractError(
        f"artifact index not found under {root}; searched {searched}"
    )


def _validate_source_pack(value: Any) -> dict[str, Any]:
    source_pack = dict(_require_mapping(value, "source_pack"))
    _require_string(source_pack.get("ref"), "source_pack.ref")
    source_type = _require_string(
        source_pack.get("source_type"),
        "source_pack.source_type",
    )
    if source_type not in SOURCE_TYPES:
        raise MillefeuilleContractError(
            f"source_pack.source_type must be one of {sorted(SOURCE_TYPES)!r}"
        )
    _require_string(source_pack.get("source_hash"), "source_pack.source_hash")
    return source_pack


def _validate_source_identity(value: Any) -> dict[str, Any]:
    source_identity = dict(_require_mapping(value, "source_identity"))
    _require_string(source_identity.get("title"), "source_identity.title")
    return source_identity


def _validate_stages(value: Any) -> dict[str, dict[str, Any]]:
    stages = _require_mapping(value, "stages")
    valid_statuses = StageStatus.values()
    result: dict[str, dict[str, Any]] = {}
    for name, record in stages.items():
        stage_name = _require_string(name, "stage name")
        stage_record = dict(_require_mapping(record, f"stages.{stage_name}"))
        status = _require_string(
            stage_record.get("status"),
            f"stages.{stage_name}.status",
        )
        if status not in valid_statuses:
            raise MillefeuilleContractError(
                f"stages.{stage_name}.status must be one of {sorted(valid_statuses)!r}"
            )
        result[stage_name] = stage_record
    return result


def _validate_artifacts(value: Any) -> dict[str, dict[str, Any]]:
    artifacts = _require_mapping(value, "artifacts")
    result: dict[str, dict[str, Any]] = {}
    for name, record in artifacts.items():
        artifact_name = _require_string(name, "artifact name")
        artifact_record = dict(_require_mapping(record, f"artifacts.{artifact_name}"))
        _require_string(artifact_record.get("kind"), f"artifacts.{artifact_name}.kind")
        _require_string(artifact_record.get("ref"), f"artifacts.{artifact_name}.ref")
        result[artifact_name] = artifact_record
    return result


def _validate_indexes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MillefeuilleContractError("indexes must be an array")
    result: list[dict[str, Any]] = []
    for idx, record in enumerate(value):
        index_record = dict(_require_mapping(record, f"indexes[{idx}]"))
        lane = _require_string(index_record.get("lane"), f"indexes[{idx}].lane")
        if lane not in INDEX_LANES:
            raise MillefeuilleContractError(
                f"indexes[{idx}].lane must be one of {sorted(INDEX_LANES)!r}"
            )
        status = _require_string(index_record.get("status"), f"indexes[{idx}].status")
        if status not in INDEX_STATUSES:
            raise MillefeuilleContractError(
                f"indexes[{idx}].status must be one of {sorted(INDEX_STATUSES)!r}"
            )
        result.append(index_record)
    return result


def _validate_writeback(value: Any) -> dict[str, Any]:
    writeback = dict(_require_mapping(value, "zotero_writeback"))
    mode = _require_string(writeback.get("mode"), "zotero_writeback.mode")
    if mode not in WRITEBACK_MODES:
        raise MillefeuilleContractError(
            f"zotero_writeback.mode must be one of {sorted(WRITEBACK_MODES)!r}"
        )
    status = _require_string(writeback.get("status"), "zotero_writeback.status")
    if status not in WRITEBACK_STATUSES:
        raise MillefeuilleContractError(
            f"zotero_writeback.status must be one of {sorted(WRITEBACK_STATUSES)!r}"
        )
    return writeback
