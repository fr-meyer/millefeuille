"""No-write GPT provenance derived from verified inputs, outputs, and usage.

Records use the strict generic model-provenance/v0.1 contract. Their input
refs point at the exact run-scoped source snapshots in the output plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import (
    MODEL_EXECUTION_EVIDENCE_SCHEMA_VERSION,
    materialize_model_provenance_record,
)
from millefeuille.domain.model_executor import validate_model_executor_result
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_observed_usage_plan import (
    plan_gpt_summary_observed_usage,
)
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs
from millefeuille.domain.summary_preparation import (
    verify_summary_preparation_package,
)


@dataclass(frozen=True)
class PlannedModelProvenanceRecord:
    ref: str
    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class SummaryModelProvenancePlan:
    paper_id: str = field(repr=False)
    run_id: str = field(repr=False)
    source_manifest_sha256: str
    write_manifest_sha256: str
    observed_usage_sha256: str
    provenance_manifest_ref: str
    provenance_manifest_sha256: str
    records: tuple[PlannedModelProvenanceRecord, ...] = field(repr=False)
    provenance_manifest_json: bytes = field(repr=False)


def plan_gpt_summary_model_provenance(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> SummaryModelProvenancePlan:
    """Materialize strict provenance in memory for one complete GPT batch."""

    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    planned = plan_gpt_summary_outputs(outcome=outcome, run_id=run_id, **evidence)
    observed = plan_gpt_summary_observed_usage(
        outcome=outcome, run_id=run_id, **evidence
    )
    if observed.write_manifest_sha256 != planned.write_manifest_sha256:
        raise MillefeuilleContractError("GPT provenance output binding drift")
    preparation = verify_summary_preparation_package(**evidence)
    write_manifest = json.loads(planned.write_manifest_json)
    entries = write_manifest["entries"]
    if not (
        len(outcome.batch.units)
        == len(outcome.executions)
        == len(planned.texts)
        == len(entries)
    ):
        raise MillefeuilleContractError("GPT provenance coverage drift")

    run_prefix = f"analyses/millefeuille/{run_id}/"
    input_refs = [
        _relative_ref(source.ref, run_prefix) for source in planned.source_inputs
    ]
    if len(input_refs) != 2 or not all(
        ref.startswith("structure/inputs/") for ref in input_refs
    ):
        raise MillefeuilleContractError("GPT provenance source refs are invalid")
    records: list[PlannedModelProvenanceRecord] = []
    manifest_entries: list[dict[str, Any]] = []
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
                "GPT provenance observed usage is unavailable"
            )
        summary_id = f"{unit.stage}-{unit.unit_id}"
        expected_ref = f"{run_prefix}summaries/provenance/{summary_id}.json"
        if (
            entry["summary_id"] != summary_id
            or entry["text_ref"] != text.ref
            or entry["text_sha256"] != text.sha256
            or entry["provenance_ref"] != expected_ref
        ):
            raise MillefeuilleContractError("GPT provenance output ref drift")
        model_plan = preparation["execution_plans"][unit.stage]
        parameters = model_plan["requested_parameters"]
        record = materialize_model_provenance_record(
            execution_plan=model_plan,
            execution_evidence={
                "schema_version": MODEL_EXECUTION_EVIDENCE_SCHEMA_VERSION,
                "profile": model_plan["profile"],
                "stage": unit.stage,
                "requested_model": result["requested_model"],
                "resolved_model": result["actual_model"],
                "provider": model_plan["provider"],
                "backend": model_plan["backend"],
                "reasoning_effort": parameters["reasoning_effort"],
                "fast_mode": parameters["fast_mode"],
                "prompt_version": parameters["prompt_version"],
                "fallback_used": result["fallback"]["used"],
                "input_refs": input_refs,
                "output_refs": [_relative_ref(text.ref, run_prefix)],
                "usage": usage,
                "quality_warnings": [],
            },
        )
        encoded = _canonical_json(record)
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        records.append(PlannedModelProvenanceRecord(expected_ref, digest, encoded))
        manifest_entries.append(
            {
                "summary_id": summary_id,
                "provenance_ref": expected_ref,
                "provenance_sha256": digest,
                "text_ref": text.ref,
                "text_sha256": text.sha256,
                "request_sha256": result["request_sha256"],
                "executor_result_sha256": entry["executor_result_sha256"],
            }
        )
    manifest = {
        "schema_version": "millefeuille-gpt-summary-model-provenance/v0.1",
        "paper_id": planned.paper_id,
        "run_id": run_id,
        "source_manifest_sha256": planned.source_manifest_sha256,
        "write_manifest_sha256": planned.write_manifest_sha256,
        "observed_usage_sha256": observed.observed_usage_sha256,
        "provenance_manifest_ref": planned.provenance_manifest_ref,
        "entries": manifest_entries,
    }
    encoded_manifest = _canonical_json(manifest)
    return SummaryModelProvenancePlan(
        paper_id=planned.paper_id,
        run_id=run_id,
        source_manifest_sha256=planned.source_manifest_sha256,
        write_manifest_sha256=planned.write_manifest_sha256,
        observed_usage_sha256=observed.observed_usage_sha256,
        provenance_manifest_ref=planned.provenance_manifest_ref,
        provenance_manifest_sha256="sha256:"
        + hashlib.sha256(encoded_manifest).hexdigest(),
        records=tuple(records),
        provenance_manifest_json=encoded_manifest,
    )


def _relative_ref(ref: str, prefix: str) -> str:
    if not ref.startswith(prefix):
        raise MillefeuilleContractError("GPT provenance ref is outside its run")
    return ref[len(prefix) :]


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
