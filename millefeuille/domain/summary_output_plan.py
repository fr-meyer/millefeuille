"""No-write bridge from a validated GPT run to hierarchical summary artifacts.

This plan holds private summary text in memory and fingerprints the prospective
files. It is neither a durable-write permit nor a complete provenance package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from millefeuille.domain.millefeuille import (
    HierarchicalSummaryRecord,
    MillefeuilleContractError,
)
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_results import accept_summary_execution_batch

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SUMMARY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_GRAIN_SCOPE = {
    "summarize_page": ("page", "general"),
    "summarize_section": ("section", "general"),
    "summarize_full_paper": ("full-paper", "classification"),
}


@dataclass(frozen=True)
class PlannedSummaryText:
    ref: str
    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class SummaryOutputPlan:
    paper_id: str
    run_id: str
    source_manifest_sha256: str
    write_manifest_sha256: str
    write_manifest_json: bytes = field(repr=False)
    summary_record: dict[str, Any] = field(repr=False)
    texts: tuple[PlannedSummaryText, ...] = field(repr=False)


def plan_gpt_summary_outputs(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> SummaryOutputPlan:
    """Revalidate the complete run and derive only in-memory output bytes.

    An approved writer must later add exact model provenance and separately
    authorize every durable file before using this plan.
    """

    if not isinstance(outcome, TrustedGptSummaryOutcome):
        raise MillefeuilleContractError("GPT summary execution outcome is invalid")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise MillefeuilleContractError("GPT summary run id is invalid")
    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    fresh = plan_grounded_gpt_summary_batch(**evidence)
    if (
        fresh.manifest.sha256 != outcome.approval.manifest_sha256
        or fresh.batch != outcome.batch
        or len(outcome.executions) != len(fresh.batch.units)
        or outcome.approval.request_count != len(fresh.batch.units)
    ):
        raise MillefeuilleContractError("GPT summary output source or scope drift")
    executions = {
        (unit.stage, unit.unit_id): execution
        for unit, execution in zip(fresh.batch.units, outcome.executions, strict=True)
    }
    accepted = accept_summary_execution_batch(
        batch=fresh.batch, executions=executions, **evidence
    )
    if accepted != outcome.accepted:
        raise MillefeuilleContractError("GPT summary accepted output drift")

    entries: list[dict[str, Any]] = []
    texts: list[PlannedSummaryText] = []
    manifest_entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for unit in accepted.units:
        summary_id = f"{unit.stage}-{unit.unit_id}"
        if not _SUMMARY_ID.fullmatch(summary_id) or summary_id in seen_ids:
            raise MillefeuilleContractError("GPT summary output identity is unsafe")
        seen_ids.add(summary_id)
        grain, scope = _GRAIN_SCOPE[unit.stage]
        text_ref = f"texts/{summary_id}.md"
        run_ref = f"summaries/{text_ref}"
        data = unit.summary.encode("utf-8")
        text_sha256 = "sha256:" + hashlib.sha256(data).hexdigest()
        entries.append(
            {
                "summary_id": summary_id,
                "grain": grain,
                "scope": scope,
                "text_ref": text_ref,
                "source_locators": list(unit.source_locators),
            }
        )
        texts.append(PlannedSummaryText(run_ref, text_sha256, data))
        result_bytes = _canonical_json(unit.executor_result)
        manifest_entries.append(
            {
                "summary_id": summary_id,
                "text_ref": run_ref,
                "text_sha256": text_sha256,
                "executor_result_sha256": "sha256:"
                + hashlib.sha256(result_bytes).hexdigest(),
            }
        )
    summary_record = HierarchicalSummaryRecord(
        paper_id=accepted.paper_id,
        run_id=run_id,
        summaries=entries,
    ).to_dict()
    write_manifest = {
        "schema_version": "millefeuille-gpt-summary-output-plan/v0.1",
        "paper_id": accepted.paper_id,
        "run_id": run_id,
        "source_manifest_sha256": outcome.approval.manifest_sha256,
        "summary_record_sha256": "sha256:"
        + hashlib.sha256(_canonical_json(summary_record)).hexdigest(),
        "entries": manifest_entries,
    }
    write_manifest_json = _canonical_json(write_manifest)
    return SummaryOutputPlan(
        paper_id=accepted.paper_id,
        run_id=run_id,
        source_manifest_sha256=outcome.approval.manifest_sha256,
        write_manifest_sha256="sha256:"
        + hashlib.sha256(write_manifest_json).hexdigest(),
        write_manifest_json=write_manifest_json,
        summary_record=summary_record,
        texts=tuple(texts),
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
