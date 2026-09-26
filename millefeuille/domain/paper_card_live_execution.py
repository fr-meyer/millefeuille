"""One receipt-bound GPT paper-card call, retaining all output in memory."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import json
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
from millefeuille.domain.paper_card_execution_scope import (
    GptCardApprovalPreview,
    validate_gpt_card_approval_preview,
)
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.paper_card_plan import (
    GptPaperCardRequestPlan,
    validate_v1_paper_card_content,
)
from millefeuille.domain.paper_card_reservation_broker import (
    request_gpt_card_reservation,
)
from millefeuille.domain.paper_card_results import (
    ValidatedGptPaperCardExecution,
    validate_published_gpt_paper_card_execution,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity


@dataclass(frozen=True)
class TrustedGptCardOutcome:
    approval: GptCardApprovalPreview
    plan: GptPaperCardRequestPlan = field(repr=False)
    validated: ValidatedGptPaperCardExecution = field(repr=False)
    execution: OpenClawModelExecution = field(repr=False)


def run_trusted_gpt_paper_card(
    *,
    source_pack_root: str | Path,
    artifact_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
    run_id: str,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    client: OpenClawModelClient | None = None,
) -> TrustedGptCardOutcome:
    """Consume one trusted reservation before one exact provider request.

    Any interrupted or failed call leaves its receipt consumed. This runner
    writes no paper text and cannot publish a canonical card or acceptance.
    """

    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("GPT card approval evidence is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    inputs = {
        "source_pack_root": source_pack_root,
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
        "publication": publication,
        "run_id": run_id,
    }
    scope = dict(inputs, artifact_root=artifact_root, packet=packet, receipt=receipt)
    preview = validate_gpt_card_approval_preview(**scope, now=now)
    plan = plan_published_gpt_paper_card_request(**inputs)
    if plan.manifest_sha256 != preview.manifest_sha256:
        raise MillefeuilleContractError("GPT card source changed before reservation")
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
        raise MillefeuilleContractError("GPT card operator preflight did not validate")
    executor = client or OpenClawModelClient()
    executor.preflight_auth(run_timeout_seconds=plan.request["timeout_seconds"])
    reserved = request_gpt_card_reservation(**scope)
    if reserved != preview:
        raise MillefeuilleContractError("GPT card broker reservation drift")
    fresh = plan_published_gpt_paper_card_request(**inputs)
    if fresh != plan:
        raise MillefeuilleContractError("GPT card source changed before dispatch")
    executor.preflight_auth(run_timeout_seconds=fresh.request["timeout_seconds"])
    execution = executor.execute(
        request=fresh.request,
        input_payload=fresh.input_payload,
        output_validator=lambda payload: validate_v1_paper_card_content(
            (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode(
                "utf-8"
            ),
            plan=fresh,
        ),
    )
    validated = validate_published_gpt_paper_card_execution(
        plan=fresh,
        execution=execution,
        **{key: value for key, value in inputs.items() if key != "run_id"},
    )
    return TrustedGptCardOutcome(reserved, fresh, validated, execution)
