"""Receipt-bound GPT summary execution with in-memory batch acceptance.

No paper output is published here. The privileged broker consumes the exact
receipt before the first possible model call; every later call uses the same
immutable planned prompt and a fresh source recheck.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from millefeuille.clients.openclaw_model_client import (
    OpenClawModelClient,
    OpenClawModelExecution,
)
from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    evaluate_operator_preflight,
)
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_execution_scope import (
    validate_gpt_summary_approval_preview,
)
from millefeuille.domain.summary_reservation_broker import (
    request_gpt_summary_reservation,
)
from millefeuille.domain.summary_results import (
    AcceptedSummaryBatch,
    accept_summary_execution_batch,
    accept_summary_execution_unit,
    validate_summary_unit_payload,
)


def run_trusted_gpt_summary_batch(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    artifact_root: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    client: OpenClawModelClient | None = None,
) -> AcceptedSummaryBatch:
    """Execute one approved GPT batch and retain validated text only in memory.

    An interrupted or failed call leaves the broker reservation consumed.
    The caller must use a separate approval for any later durable write.
    """

    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("GPT summary approval evidence is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    preview = validate_gpt_summary_approval_preview(
        **evidence,
        artifact_root=artifact_root,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
        now=now,
    )
    plan = plan_grounded_gpt_summary_batch(**evidence)
    if (
        plan.manifest.sha256 != preview.manifest_sha256
        or len(plan.batch.units) != preview.request_count
    ):
        raise MillefeuilleContractError("GPT summary batch changed before reservation")

    preflight = evaluate_operator_preflight(
        packet,
        explicit_mode="approved-live",
        approval_receipt=receipt,
        environment=environment,
        now=now,
    )
    if (
        any(row.state != "present" for row in preflight.credential_readiness)
        or preflight.decision != "approved-scope-validated-execution-unsupported"
        or preflight.blockers != ("external-execution-unsupported",)
        or preflight.approval_receipt is None
        or preflight.approval_receipt.receipt_id != receipt.receipt_id
        or preflight.approval_receipt.content_digest != receipt.content_digest
        or any(check.status != "passed" for check in preflight.checks)
    ):
        raise MillefeuilleContractError(
            "GPT summary operator preflight did not validate"
        )

    executor = client or OpenClawModelClient()
    executor.preflight_auth(
        run_timeout_seconds=max(
            unit.request["timeout_seconds"] for unit in plan.batch.units
        )
    )
    reserved = request_gpt_summary_reservation(
        **evidence,
        artifact_root=artifact_root,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
    )
    if reserved != preview:
        raise MillefeuilleContractError("GPT summary broker reservation drift")

    executions: dict[tuple[str, str], OpenClawModelExecution] = {}
    for unit in plan.batch.units:
        # The approved manifest commits to every prompt, model and work unit.
        fresh = plan_grounded_gpt_summary_batch(**evidence)
        if fresh.manifest.sha256 != preview.manifest_sha256:
            raise MillefeuilleContractError("GPT summary source changed before a call")
        executor.preflight_auth(run_timeout_seconds=unit.request["timeout_seconds"])
        execution = executor.execute(
            request=unit.request,
            input_payload=unit.input_payload,
            output_validator=lambda payload, selected=unit: (
                validate_summary_unit_payload(selected, payload)
            ),
        )
        accept_summary_execution_unit(unit=unit, execution=execution)
        executions[(unit.stage, unit.unit_id)] = execution

    return accept_summary_execution_batch(
        batch=plan.batch,
        executions=executions,
        **evidence,
    )
