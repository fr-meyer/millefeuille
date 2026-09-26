"""No-effect scope check for a future GPT summary batch execution receipt.

This module verifies approval metadata only. A receipt's self-hash and this
preview never reserve authorization or permit a provider call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
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
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch

_STOP_CONDITIONS = (
    "auth-failure",
    "model-mismatch",
    "provider-error",
    "schema-failure",
    "source-drift",
)
_DISPOSAL = ("not-applicable", "never-persist", "delete-after-run")


@dataclass(frozen=True)
class SummaryApprovalPreview:
    manifest_sha256: str
    request_count: int
    packet_digest: str
    receipt_digest: str
    receipt_id: str
    paper_id: str = field(repr=False)
    provider_calls_performed: int = 0


def validate_gpt_summary_approval_preview(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    artifact_root: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> SummaryApprovalPreview:
    """Replan source and require an exact, unexpired, one-paper GPT scope.

    This is a review-only check. Live execution must additionally verify an
    administrator-owned approval and durably reserve the receipt before calls.
    """

    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("summary approval evidence is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    validate_gpt_summary_evidence_paths(
        evidence={
            "route_evidence_path": str(route_evidence_path),
            "structure_evidence_path": str(structure_evidence_path),
            "preparation_path": str(preparation_path),
        },
        source_pack_root=str(source_pack_root),
    )
    plan = plan_grounded_gpt_summary_batch(
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    manifest_sha256 = plan.manifest.sha256
    destination = LiveTarget(
        "artifact-root", compute_operator_root_target_id(str(artifact_root))
    )
    expected_targets = tuple(
        sorted(
            (
                destination,
                LiveTarget("paper-id", plan.batch.paper_id),
                LiveTarget("summary-manifest", manifest_sha256),
                LiveTarget(
                    "preflight-scope",
                    compute_operator_authorization_context_digest(packet.to_dict()),
                ),
            )
        )
    )
    source = packet.source
    provider = packet.provider
    disposal = packet.disposal_policy
    if (
        packet.mode != "approved-live"
        or packet.operations != ("model.summarize",)
        or source.adapter != "local-fixture"
        or (source.selector.kind, source.selector.value)
        != ("batch-manifest", manifest_sha256)
        or source.item_cap != 1
        or source.resolved_item_count != 1
        or packet.targets != expected_targets
        or packet.destinations != (destination,)
        or packet.artifact_root != str(artifact_root)
        or packet.source_pack_root != str(source_pack_root)
        or provider is None
        or (provider.provider_id, provider.model_id, provider.profile_id)
        != ("openai", "gpt-5.6-sol", "openclaw-subscription-oauth")
        or packet.max_provider_calls != len(plan.batch.units)
        or packet.max_cost_usd_micros != 0
        or (disposal.pdfs, disposal.provider_payloads, disposal.temporary_files)
        != _DISPOSAL
        or packet.stop_conditions != _STOP_CONDITIONS
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
        raise MillefeuilleContractError("packet is outside the GPT summary scope")
    validate_approved_live_receipt_for_no_effect(
        receipt, packet.to_approved_live_request(), now=now
    )
    return SummaryApprovalPreview(
        manifest_sha256=manifest_sha256,
        request_count=len(plan.batch.units),
        packet_digest=packet.content_digest,
        receipt_digest=receipt.content_digest,
        receipt_id=receipt.receipt_id,
        paper_id=plan.batch.paper_id,
    )


def validate_gpt_summary_evidence_paths(
    *, evidence: dict[str, str], source_pack_root: str
) -> None:
    """Reject unusable broker paths before replanning or approving a batch.

    This checks lexical containment only. Source planning and the privileged
    broker still perform their no-follow reads and authorization checks.
    """

    root = Path(source_pack_root)
    if not root.is_absolute() or os.path.normpath(source_pack_root) != source_pack_root:
        raise MillefeuilleContractError("GPT summary source root is invalid")
    for value in evidence.values():
        path = Path(value)
        if not path.is_absolute() or os.path.normpath(value) != value:
            raise MillefeuilleContractError("GPT summary evidence path is invalid")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise MillefeuilleContractError(
                "GPT summary evidence path is outside its source root"
            ) from exc
        if not relative.parts:
            raise MillefeuilleContractError("GPT summary evidence path is invalid")
