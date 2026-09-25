"""No-write observed GPT usage evidence bound to one summary output plan.

The record contains only model identities, request/result hashes, and actual
token counts. It is not a substitute for the full model-provenance contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import validate_model_executor_result
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs


@dataclass(frozen=True)
class SummaryObservedUsagePlan:
    paper_id: str = field(repr=False)
    run_id: str = field(repr=False)
    source_manifest_sha256: str
    write_manifest_sha256: str
    observed_usage_ref: str
    observed_usage_sha256: str
    unit_count: int
    observed_usage_json: bytes = field(repr=False)


def plan_gpt_summary_observed_usage(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> SummaryObservedUsagePlan:
    """Require actual usage for every accepted unit and bind it to output."""

    planned = plan_gpt_summary_outputs(
        outcome=outcome,
        run_id=run_id,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    write_manifest = json.loads(planned.write_manifest_json)
    entries = write_manifest["entries"]
    if not (
        len(outcome.batch.units)
        == len(outcome.executions)
        == len(planned.texts)
        == len(entries)
    ):
        raise MillefeuilleContractError("GPT summary usage coverage drift")
    observed: list[dict[str, Any]] = []
    for unit, execution, text, entry in zip(
        outcome.batch.units,
        outcome.executions,
        planned.texts,
        entries,
        strict=True,
    ):
        result = validate_model_executor_result(
            request=unit.request, result=execution.result
        )
        usage = result["usage"]
        if usage is None:
            raise MillefeuilleContractError(
                "GPT summary observed token usage is unavailable"
            )
        if (
            entry["text_ref"] != text.ref
            or entry["text_sha256"] != text.sha256
            or entry["summary_id"] != f"{unit.stage}-{unit.unit_id}"
        ):
            raise MillefeuilleContractError("GPT summary usage output binding drift")
        observed.append(
            {
                "summary_id": entry["summary_id"],
                "text_ref": text.ref,
                "text_sha256": text.sha256,
                "executor_result_sha256": entry["executor_result_sha256"],
                "request_sha256": result["request_sha256"],
                "input_sha256": unit.request["input"]["sha256"],
                "output_sha256": result["output"]["sha256"],
                "requested_model": result["requested_model"],
                "actual_model": result["actual_model"],
                "usage": usage,
            }
        )
    manifest = {
        "schema_version": "millefeuille-gpt-summary-observed-usage/v0.1",
        "paper_id": planned.paper_id,
        "run_id": run_id,
        "source_manifest_sha256": planned.source_manifest_sha256,
        "write_manifest_sha256": planned.write_manifest_sha256,
        "observed_usage_ref": planned.observed_usage_ref,
        "entries": observed,
    }
    encoded = _canonical_json(manifest)
    return SummaryObservedUsagePlan(
        paper_id=planned.paper_id,
        run_id=run_id,
        source_manifest_sha256=planned.source_manifest_sha256,
        write_manifest_sha256=planned.write_manifest_sha256,
        observed_usage_ref=planned.observed_usage_ref,
        observed_usage_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        unit_count=len(observed),
        observed_usage_json=encoded,
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
