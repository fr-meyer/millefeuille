"""One exact Astra call, no persistence; publication is independently approved."""

from dataclasses import dataclass, field
import json

from millefeuille.clients.openclaw_model_client import (
    OpenClawModelClient,
    OpenClawModelExecution,
)
from millefeuille.domain.classification_live_scope import (
    canonical,
    digest,
    identity,
    prepare,
    validate_scope,
)
from millefeuille.domain.classification_model import (
    validate_classification_model_output,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.model_executor import validate_model_executor_result
from millefeuille.domain.operator_preflight import evaluate_operator_preflight
from millefeuille.domain.taxonomy import validate_external_classification_taxonomy


@dataclass(frozen=True)
class ClassificationOutcome:
    execution_receipt_digest: str
    plan_sha256: str
    execution: OpenClawModelExecution = field(repr=False)


def validated_files(*, inputs, outcome):
    plan = prepare(inputs)
    if (
        not isinstance(outcome, ClassificationOutcome)
        or identity(plan) != outcome.plan_sha256
    ):
        raise Error("classification source/plan drift")
    execution = outcome.execution
    result = validate_model_executor_result(
        request=plan.executor_request(), result=execution.result
    )
    if (
        result["status"] != "succeeded"
        or result["usage"] is None
        or result["cost"] not in (None, {"currency": "USD", "micro_usd": 0})
        or not isinstance(execution.output, bytes)
        or result["output"]
        != {"sha256": digest(execution.output), "bytes": len(execution.output)}
    ):
        raise Error("classification result/usage/output mismatch")
    if len(execution.output) > 131072:
        raise Error("classification output size")
    value = validate_classification_model_output(
        json.loads(execution.output), plan=plan
    )
    entries = {
        e["entry_id"]: e for e in plan.taxonomy_lock["registry_snapshot"]["entries"]
    }

    def path(entry_id):
        if entry_id is None:
            return None
        entry = entries[entry_id]
        return entries[entry["parent_id"]]["label"] + " > " + entry["label"]

    evidence = {
        "schema_version": "millefeuille-classification-fixture-evidence/v0.1",
        "taxonomy_version": value["taxonomy_version"],
        "taxonomy_lock": plan.taxonomy_lock,
        "taxonomy_lock_identity": value["taxonomy_lock_identity"],
        "primary_entry_id": value["primary_entry_id"],
        "primary_path": path(value["primary_entry_id"]),
        "strongest_rejected_entry_id": value["strongest_rejected_entry_id"],
        "strongest_rejected_path": path(value["strongest_rejected_entry_id"]),
        "confidence": value["confidence"],
        "review_reasons": value["review_reasons"],
        "adjudication_required": value["taxonomy_gap"] or value["confidence"] == "low",
        "evidence_refs": [row["path"] for row in plan.input_files],
        "qa_flags": [],
        "rejected_alternatives": [],
    }
    validate_external_classification_taxonomy(evidence, run_id=plan.run_id)
    provenance = {
        "schema": "millefeuille-classification-provenance/v1",
        "plan_sha256": identity(plan),
        "execution_receipt_digest": outcome.execution_receipt_digest,
        "request": plan.request,
        "result": result,
    }
    prefix = "classification-" + identity(plan).split(":")[1]
    files = {
        prefix + ".proposal.json": canonical(value),
        prefix + ".evidence.json": canonical(evidence),
        prefix + ".provenance.json": canonical(provenance),
    }
    if sum(map(len, files.values())) > 1024 * 1024:
        raise Error("classification publication exceeds budget")
    return files


def output_identity(files):
    return digest(
        canonical(
            {
                ref: {"sha256": digest(data), "bytes": len(data)}
                for ref, data in sorted(files.items())
            }
        )
    )


def execute_classification(*, inputs, packet, receipt):
    from millefeuille.domain.classification_live_broker import request

    scope = validate_scope(inputs=inputs, packet=packet, receipt=receipt)
    plan = prepare(inputs)
    if identity(plan) != scope.plan_sha256:
        raise Error("classification changed before execution")
    checked = evaluate_operator_preflight(
        packet, explicit_mode="approved-live", approval_receipt=receipt
    )
    if (
        checked.decision != "approved-scope-validated-execution-unsupported"
        or checked.blockers != ("external-execution-unsupported",)
        or any(c.status != "passed" for c in checked.checks)
        or any(c.state != "present" for c in checked.credential_readiness)
    ):
        raise Error("classification operator readiness denied")
    client = OpenClawModelClient()
    client.preflight_auth(run_timeout_seconds=plan.request["timeout_seconds"])
    request(inputs=inputs, packet=packet, receipt=receipt)
    fresh = prepare(inputs)
    if fresh != plan:
        raise Error("classification changed after reservation")
    client.preflight_auth(run_timeout_seconds=fresh.request["timeout_seconds"])
    execution = client.execute(
        request=fresh.executor_request(),
        input_payload=fresh.prompt,
        output_validator=lambda value: validate_classification_model_output(
            value, plan=fresh
        ),
    )
    outcome = ClassificationOutcome(scope.receipt_digest, scope.plan_sha256, execution)
    validated_files(inputs=inputs, outcome=outcome)
    return outcome
