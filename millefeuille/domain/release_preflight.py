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
    artifact_root: str | Path | None = None,
    stage_manifest: str | Path | None = None,
    writeback_disposition: dict | None = None,
    approved_disposition_identity: str | None = None,
    input_admission=None,
    code_admission=None,
) -> ReleasePreflightResult:
    resolved = resolve_run_artifacts(
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
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
    no_writeback = False
    if writeback_disposition is not None:
        validate_no_writeback_disposition(
            writeback_disposition,
            resolved=resolved,
            approved_identity=approved_disposition_identity,
            current_version=current_version,
            candidate_version=candidate_version,
            input_admission=input_admission,
            code_admission=code_admission,
        )
        no_writeback = True
    readiness = (
        "ready-for-rc-review"
        if {"acceptance", "classify"}.issubset(set(completed_stages))
        and ("writeback" in completed_stages or no_writeback)
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


def no_writeback_binding(*, resolved, current_version, candidate_version):
    """Relevant local state, not absence of remote writeback evidence."""
    from millefeuille.domain.acceptance_output_plan import json_bytes
    from millefeuille.domain.secure_io import read_bytes_no_follow
    from millefeuille.domain.semantic_acceptance import digest

    refs = [
        "reports/acceptance-summary.json",
        "reports/semantic-decision.json",
        "classification/classification-plan.json",
        "classification/decision-records/" + resolved.paper_id + ".json",
        "classification/zotero-writeback-preview.json",
    ]
    return {
        "paper_id": resolved.paper_id,
        "run_id": resolved.run_id,
        "source_hash": resolved.source_hash,
        "current_version": current_version,
        "candidate_version": candidate_version,
        "stage_manifest": digest(json_bytes(resolved.stage_manifest.to_dict())),
        "artifacts": {
            ref: digest(
                read_bytes_no_follow(
                    resolved.run_dir / ref, "no-writeback binding", max_bytes=2097152
                )
            )
            for ref in refs
        },
    }


def validate_no_writeback_disposition(
    disposition,
    *,
    resolved,
    approved_identity,
    current_version,
    candidate_version,
    input_admission=None,
    code_admission=None,
):
    """Approval identity must be provided by the authenticated caller separately."""
    from millefeuille.domain.acceptance_output_plan import json_bytes
    from millefeuille.domain.millefeuille import MillefeuilleContractError
    from millefeuille.domain.semantic_acceptance import digest
    from millefeuille.domain.semantic_publication_orchestrator import (
        verify_bound_manifest,
    )

    migrated = input_admission is not None or code_admission is not None
    expected = {
        "schema": (
            "millefeuille-writeback-disposition/v2"
            if migrated
            else "millefeuille-writeback-disposition/v1"
        ),
        "enabled": False,
        "disposition": "explicit_approved_no_writeback",
        "remote_operations": 0,
        "release_preflight_consumer_result": "pass",
    }
    if (
        not isinstance(disposition, dict)
        or set(disposition)
        != set(expected)
        | {"approval_identity", "binding", "code_manifest", "input_manifest"}
        | ({"input_admission", "code_admission"} if migrated else set())
        or any(disposition.get(k) != v for k, v in expected.items())
        or not isinstance(disposition.get("approval_identity"), str)
        or not disposition["approval_identity"].strip()
        or not approved_identity
        or digest(json_bytes(disposition)) != approved_identity
    ):
        raise MillefeuilleContractError(
            "explicit hash-bound no-writeback approval required"
        )
    if migrated:
        from millefeuille.domain.semantic_publication_orchestrator import (
            WorkloadAdmission,
        )

        for kind, admission in [("input", input_admission), ("code", code_admission)]:
            if (
                not isinstance(admission, WorkloadAdmission)
                or disposition[kind + "_admission"] != admission.binding()
            ):
                raise MillefeuilleContractError("no-writeback workload admission drift")
    verify_bound_manifest(disposition["code_manifest"], admission=code_admission)
    verify_bound_manifest(disposition["input_manifest"], admission=input_admission)
    if disposition["binding"] != no_writeback_binding(
        resolved=resolved,
        current_version=current_version,
        candidate_version=candidate_version,
    ):
        raise MillefeuilleContractError("no-writeback disposition drift")
