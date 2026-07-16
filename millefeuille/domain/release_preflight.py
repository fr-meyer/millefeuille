"""Local release-candidate preflight artifacts for Millefeuille."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from millefeuille.domain.millefeuille import (
    ReleaseCandidatePreflightRecord,
    StageStatus,
)
from millefeuille.domain.stage_runtime import (
    persist_run_artifacts,
    relative_ref,
    resolve_run_artifacts,
    upsert_artifact_record,
    write_json_object,
    write_text,
)

RELEASE_PREFLIGHT_JSON_REF = Path("reports/release-candidate-preflight.json")
RELEASE_PREFLIGHT_MD_REF = Path("reports/release-candidate-preflight.md")


@dataclass(frozen=True)
class ReleasePreflightResult:
    paper_id: str
    run_id: str
    readiness: str
    json_path: Path
    markdown_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "run_id": self.run_id,
            "readiness": self.readiness,
            "json_path": str(self.json_path),
            "markdown_path": str(self.markdown_path),
        }


def write_release_candidate_preflight(
    *,
    source_pack_root: str | Path,
    run_id: str,
    repo_root: str | Path,
    paper_id: str | None = None,
    item_key: str | None = None,
    candidate_version: str | None = None,
) -> ReleasePreflightResult:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
    )
    pyproject = tomllib.loads(
        (Path(repo_root) / "pyproject.toml").read_text(encoding="utf-8")
    )
    current_version = str(pyproject["project"]["version"])
    completed_stages = [
        stage.name.value
        for stage in resolved.stage_manifest.stages
        if stage.status == StageStatus.PASSED
    ]
    manual_gates_remaining = [
        gate.value for gate in resolved.stage_manifest.manual_gates
    ]
    readiness = (
        "ready-for-rc-review"
        if {"acceptance", "classify", "writeback"}.issubset(set(completed_stages))
        else "needs-review"
    )
    record = ReleaseCandidatePreflightRecord(
        current_version=current_version,
        candidate_version=candidate_version,
        completed_stages=completed_stages,
        manual_gates_remaining=manual_gates_remaining,
        readiness=readiness,
        validations=[
            "offline fixture validation only",
            "dev to main promotion remains manual gate",
            "release tag and package publication remain manual gates",
        ],
    )
    json_path = resolved.run_dir / RELEASE_PREFLIGHT_JSON_REF
    markdown_path = resolved.run_dir / RELEASE_PREFLIGHT_MD_REF
    write_json_object(json_path, record.to_dict())
    write_text(
        markdown_path, _render_markdown(record, resolved.paper_id, resolved.run_id)
    )

    artifact_index = upsert_artifact_record(
        resolved.artifact_index,
        name="release_candidate_preflight",
        kind="release-candidate-preflight",
        ref=relative_ref(json_path, resolved.run_dir),
        format="json",
        stage="release",
        private_content=False,
    )
    artifact_index = upsert_artifact_record(
        artifact_index,
        name="release_candidate_preflight_markdown",
        kind="release-candidate-preflight-markdown",
        ref=relative_ref(markdown_path, resolved.run_dir),
        format="markdown",
        stage="release",
        private_content=False,
    )
    persist_run_artifacts(
        resolved,
        stage_manifest=resolved.stage_manifest,
        artifact_index=artifact_index,
    )
    return ReleasePreflightResult(
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        readiness=readiness,
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _render_markdown(
    record: ReleaseCandidatePreflightRecord,
    paper_id: str,
    run_id: str,
) -> str:
    lines = [
        "# Release Candidate Preflight",
        "",
        f"- Paper: `{paper_id}`",
        f"- Run: `{run_id}`",
        f"- Current version: `{record.current_version}`",
        f"- Readiness: `{record.readiness}`",
        "",
        "## Completed Stages",
    ]
    for stage in record.completed_stages:
        lines.append(f"- `{stage}`")
    lines.extend(["", "## Remaining Manual Gates"])
    if record.manual_gates_remaining:
        for gate in record.manual_gates_remaining:
            lines.append(f"- `{gate}`")
    else:
        lines.append("- `[none recorded in stage manifest]`")
    return "\n".join(lines) + "\n"
