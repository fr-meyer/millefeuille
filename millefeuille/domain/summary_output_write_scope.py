"""No-effect approval preview for a complete GPT summary output bundle.

The preview identifies the exact source, output bytes, and prospective files.
It never reserves a receipt or writes private summary content.
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
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_output_plan import plan_gpt_summary_outputs

_STOP_CONDITIONS = ("source-drift", "write-failure")
_DISPOSAL = ("not-applicable", "not-applicable", "delete-after-run")


@dataclass(frozen=True)
class SummaryOutputWritePreview:
    source_manifest_sha256: str
    write_manifest_sha256: str
    paper_id: str = field(repr=False)
    run_id: str = field(repr=False)
    file_refs: tuple[str, ...] = field(repr=False)
    packet_digest: str
    receipt_digest: str
    receipt_id: str
    files_written: int = 0


def validate_gpt_summary_output_write_preview(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> SummaryOutputWritePreview:
    """Replan every output and require an exact, unexpired one-paper write scope."""

    if not isinstance(packet, OperatorPreflightPacket) or not isinstance(
        receipt, ApprovedLiveReceipt
    ):
        raise MillefeuilleContractError("summary output write approval is invalid")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    root = str(source_pack_root)
    _require_evidence_under_root(
        root,
        route_evidence_path,
        structure_evidence_path,
        preparation_path,
    )
    plan = plan_gpt_summary_outputs(
        outcome=outcome,
        run_id=run_id,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    destination = LiveTarget("source-pack-root", compute_operator_root_target_id(root))
    expected_targets = tuple(
        sorted(
            (
                destination,
                LiveTarget("paper-id", plan.paper_id),
                LiveTarget("summary-output-manifest", plan.write_manifest_sha256),
                LiveTarget(
                    "preflight-scope",
                    compute_operator_authorization_context_digest(packet.to_dict()),
                ),
            )
        )
    )
    source = packet.source
    disposal = packet.disposal_policy
    if (
        packet.mode != "approved-live"
        or packet.operations != ("source-pack.write",)
        or packet.run_id != run_id
        or source.adapter != "source-pack"
        or (source.selector.kind, source.selector.value)
        != ("batch-manifest", plan.write_manifest_sha256)
        or source.item_cap != 1
        or source.resolved_item_count != 1
        or packet.targets != expected_targets
        or packet.destinations != (destination,)
        or packet.artifact_root != root
        or packet.source_pack_root != root
        or packet.provider is not None
        or packet.max_provider_calls != 0
        or packet.max_cost_usd_micros != 0
        or (disposal.pdfs, disposal.provider_payloads, disposal.temporary_files)
        != _DISPOSAL
        or packet.stop_conditions != _STOP_CONDITIONS
        or packet.rollback_actions != ("stop-and-review",)
        or packet.acceptance_status != "not-applicable"
        or packet.credential_requirements != ()
        or packet.approval_receipt is None
        or packet.approval_receipt.receipt_id != receipt.receipt_id
        or packet.approval_receipt.content_digest != receipt.content_digest
        or receipt.approval.approver_id != "fr-meyer"
    ):
        raise MillefeuilleContractError(
            "packet is outside GPT summary output write scope"
        )
    validate_approved_live_receipt_for_no_effect(
        receipt, packet.to_approved_live_request(), now=now
    )
    refs = tuple(
        sorted(
            (
                plan.write_manifest_ref,
                plan.summary_record_ref,
                *(text.ref for text in plan.texts),
            )
        )
    )
    if len(refs) != len(set(refs)):
        raise MillefeuilleContractError("GPT summary output file refs collide")
    return SummaryOutputWritePreview(
        source_manifest_sha256=plan.source_manifest_sha256,
        write_manifest_sha256=plan.write_manifest_sha256,
        paper_id=plan.paper_id,
        run_id=run_id,
        file_refs=refs,
        packet_digest=packet.content_digest,
        receipt_digest=receipt.content_digest,
        receipt_id=receipt.receipt_id,
    )


def _require_evidence_under_root(root: str, *paths: str | Path) -> None:
    base = Path(root)
    if not base.is_absolute() or os.path.normpath(root) != root:
        raise MillefeuilleContractError("summary output source root is invalid")
    for item in paths:
        path = Path(item)
        if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
            raise MillefeuilleContractError("summary output evidence path is invalid")
        try:
            relative = path.relative_to(base)
        except ValueError as exc:
            raise MillefeuilleContractError(
                "summary output evidence is outside its source root"
            ) from exc
        if not relative.parts:
            raise MillefeuilleContractError("summary output evidence path is invalid")
        read_bytes_no_follow(path, "summary output evidence")
