"""No-effect exact MF-100 scope for five validated GPT card artifacts."""

from dataclasses import dataclass, field
from datetime import datetime
import json
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
from millefeuille.domain.paper_card_live_execution import TrustedGptCardOutcome
from millefeuille.domain.paper_card_output_plan import plan_gpt_paper_card_outputs
from millefeuille.domain.paper_card_reservation_broker import _require_evidence_paths
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity


@dataclass(frozen=True)
class GptCardOutputWritePreview:
    paper_id: str
    run_id: str
    source_manifest_sha256: str
    request_plan_sha256: str
    summary_publication_sha256: str
    write_manifest_sha256: str
    provenance_sha256: str
    packet_digest: str
    receipt_digest: str
    receipt_id: str
    total_bytes: int
    file_refs: tuple[str, ...] = field(repr=False)
    files_written: int = 0


def validate_gpt_card_output_write_preview(
    *,
    outcome: TrustedGptCardOutcome,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> GptCardOutputWritePreview:
    """Replan source/result/bytes and check a one-paper zero-call write scope."""
    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("GPT card output approval is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    root = str(source_pack_root)
    evidence = {
        "route_evidence_path": str(route_evidence_path),
        "structure_evidence_path": str(structure_evidence_path),
        "preparation_path": str(preparation_path),
    }
    _require_evidence_paths(evidence, root)
    plan = plan_gpt_paper_card_outputs(
        outcome=outcome,
        source_pack_root=source_pack_root,
        publication=publication,
        **evidence,
    )
    manifest = json.loads(
        next(item.data for item in plan.files if item.ref == plan.write_manifest_ref)
    )
    destination = LiveTarget("source-pack-root", compute_operator_root_target_id(root))
    targets = tuple(
        sorted(
            (
                destination,
                LiveTarget("paper-id", plan.paper_id),
                LiveTarget("analysis-run", plan.run_id),
                LiveTarget("card-output-manifest", plan.write_manifest_sha256),
                LiveTarget("card-request-manifest", plan.request_plan_sha256),
                LiveTarget("summary-publication", plan.summary_publication_sha256),
                LiveTarget(
                    "preflight-scope",
                    compute_operator_authorization_context_digest(packet.to_dict()),
                ),
            )
        )
    )
    source, disposal = packet.source, packet.disposal_policy
    if (
        packet.mode != "approved-live"
        or packet.operations != ("source-pack.write",)
        or packet.run_id != plan.run_id
        or source.adapter != "source-pack"
        or (source.selector.kind, source.selector.value)
        != ("batch-manifest", plan.write_manifest_sha256)
        or source.item_cap != 1
        or source.resolved_item_count != 1
        or packet.targets != targets
        or packet.destinations != (destination,)
        or packet.artifact_root != root
        or packet.source_pack_root != root
        or packet.provider is not None
        or packet.max_provider_calls != 0
        or packet.max_cost_usd_micros != 0
        or (disposal.pdfs, disposal.provider_payloads, disposal.temporary_files)
        != ("not-applicable", "not-applicable", "delete-after-run")
        or packet.stop_conditions != ("source-drift", "write-failure")
        or packet.rollback_actions != ("stop-and-review",)
        or packet.acceptance_status != "not-applicable"
        or packet.credential_requirements != ()
        or packet.approval_receipt is None
        or packet.approval_receipt.receipt_id != receipt.receipt_id
        or packet.approval_receipt.content_digest != receipt.content_digest
        or receipt.approval.approver_id != "fr-meyer"
    ):
        raise MillefeuilleContractError("packet is outside GPT card output write scope")
    validate_approved_live_receipt_for_no_effect(
        receipt, packet.to_approved_live_request(), now=now
    )
    refs = tuple(item.ref for item in plan.files)
    if len(refs) != 5 or refs != tuple(sorted(set(refs))):
        raise MillefeuilleContractError("GPT card output file scope drift")
    return GptCardOutputWritePreview(
        plan.paper_id,
        plan.run_id,
        manifest["source_pack_manifest_sha256"],
        plan.request_plan_sha256,
        plan.summary_publication_sha256,
        plan.write_manifest_sha256,
        plan.provenance_sha256,
        packet.content_digest,
        receipt.content_digest,
        receipt.receipt_id,
        plan.total_bytes,
        refs,
    )
