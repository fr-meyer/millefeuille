"""No-effect MF-100 scope validation for one grounded GPT paper-card call.

A matching packet/receipt is review metadata only. A trusted one-use root
reservation and the live executor boundary are still required before a call.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    LiveTarget,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    compute_operator_authorization_context_digest,
    compute_operator_root_target_id,
)
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity

CARD_EXECUTION_STOP_CONDITIONS = (
    "auth-failure",
    "model-mismatch",
    "provider-error",
    "schema-failure",
    "source-drift",
    "usage-missing",
)


@dataclass(frozen=True)
class GptCardApprovalPreview:
    paper_id: str
    run_id: str
    manifest_sha256: str
    summary_publication_sha256: str
    packet_digest: str
    receipt_digest: str
    receipt_id: str
    request_count: int = 1
    provider_calls_performed: int = 0
    writes_performed: int = 0


def validate_gpt_card_approval_preview(
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
    now: datetime | None = None,
) -> GptCardApprovalPreview:
    """Replan the exact published input and check a one-paper, one-call scope.

    No OAuth credential is read, receipt reserved, provider called or paper
    artifact written. This does not establish administrator approval.
    """

    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("GPT card approval evidence is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    if str(artifact_root) != str(source_pack_root):
        raise MillefeuilleContractError("GPT card roots must match the source pack")
    plan = plan_published_gpt_paper_card_request(
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
        run_id=run_id,
    )
    destination = LiveTarget(
        "artifact-root", compute_operator_root_target_id(str(artifact_root))
    )
    expected_targets = tuple(
        sorted(
            (
                destination,
                LiveTarget("paper-id", plan.paper_id),
                LiveTarget("analysis-run", plan.run_id),
                LiveTarget("card-manifest", plan.manifest_sha256),
                LiveTarget("summary-publication", plan.publication_manifest_sha256),
                LiveTarget(
                    "preflight-scope",
                    compute_operator_authorization_context_digest(packet.to_dict()),
                ),
            )
        )
    )
    provider = packet.provider
    disposal = packet.disposal_policy
    source = packet.source
    if (
        packet.mode != "approved-live"
        or packet.operations != ("model.card",)
        or packet.run_id != plan.run_id
        or source.adapter != "local-fixture"
        or (source.selector.kind, source.selector.value)
        != ("batch-manifest", plan.manifest_sha256)
        or source.item_cap != 1
        or source.resolved_item_count != 1
        or packet.targets != expected_targets
        or packet.destinations != (destination,)
        or packet.artifact_root != str(artifact_root)
        or packet.source_pack_root != str(source_pack_root)
        or provider is None
        or (provider.provider_id, provider.model_id, provider.profile_id)
        != ("openai", "gpt-5.6-sol", "openclaw-subscription-oauth")
        or packet.max_provider_calls != 1
        or packet.max_cost_usd_micros != 0
        or (disposal.pdfs, disposal.provider_payloads, disposal.temporary_files)
        != ("not-applicable", "never-persist", "delete-after-run")
        or packet.stop_conditions != CARD_EXECUTION_STOP_CONDITIONS
        or packet.rollback_actions != ("stop-and-review",)
        or packet.acceptance_status != "not-applicable"
        or len(packet.credential_requirements) != 1
        or (
            packet.credential_requirements[0].credential_type,
            packet.credential_requirements[0].reference,
        )
        != ("model-oauth", "OPENCLAW_CODEX_OAUTH_READY")
        or packet.approval_receipt is None
        or packet.approval_receipt.receipt_id != receipt.receipt_id
        or packet.approval_receipt.content_digest != receipt.content_digest
        or receipt.approval.approver_id != "fr-meyer"
    ):
        raise MillefeuilleContractError("packet is outside the GPT card scope")
    validate_approved_live_receipt_for_no_effect(
        receipt, packet.to_approved_live_request(), now=now
    )
    return GptCardApprovalPreview(
        plan.paper_id,
        plan.run_id,
        plan.manifest_sha256,
        plan.publication_manifest_sha256,
        packet.content_digest,
        receipt.content_digest,
        receipt.receipt_id,
    )
