"""Exact independent scientific acceptance receipt, never proposal publication.

This validates an administrator-authenticated receipt against independently read
materialization. It does not issue approvals or manufacture a receipt from hashes.
"""

from datetime import UTC, datetime

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    LiveDisposalPolicy,
    LiveSelector,
    LiveTarget,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _hash
from millefeuille.domain.writeback_http import _checked_observation
from millefeuille.domain.writeback_live import EVIDENCE_ROOT, OUTPUT_ROOT, STOPS


def acceptance_request(plan, files):
    p = plan.value()
    return ApprovedLiveRequest(
        run_id=p["run_id"],
        operations=("science.accept-writeback-materialization",),
        targets=tuple(
            sorted(
                (
                    LiveTarget("writeback-plan-v1", plan.digest),
                    LiveTarget("materialization-files", _hash(files)),
                    LiveTarget("source-hash", p["source_hash"]),
                )
            )
        ),
        item_cap=1,
        selected_item_count=1,
        selector=LiveSelector("batch-manifest", plan.digest),
        output_root=str(OUTPUT_ROOT),
        source_pack_root=str(EVIDENCE_ROOT),
        provider=None,
        provider_call_limit=0,
        cost_limit_usd_micros=0,
        disposal_policy=LiveDisposalPolicy(
            "not-applicable", "not-applicable", "delete-after-run"
        ),
        stop_conditions=STOPS,
    )


def validate_acceptance(plan, files, supplied, authenticated):
    if supplied != authenticated:
        raise Error("independent science acceptance not authenticated")
    receipt = ApprovedLiveReceipt.from_dict(supplied)
    if receipt.approval.approver_id != "fr-meyer":
        raise Error("science approver mismatch")
    validate_approved_live_receipt_for_no_effect(
        receipt, acceptance_request(plan, files)
    )
    return receipt.content_digest


def validate_source_observations(plan, original, fresh, *, captured_at, now=None):
    """Custody must authenticate both observations and acquisition timestamp.

    Raw JSON proof is structural evidence, NOT remote authentication by itself.
    Fresh proof must come from the trusted service's uncached read, not caller input.
    """
    source, _ = _checked_observation(original)
    current, _ = _checked_observation(fresh)
    p = plan.value()
    instant = datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    age = ((now or datetime.now(UTC)) - instant).total_seconds()
    if not 0 <= age <= 60:
        raise Error("fresh source observation expired")
    if (
        source["version"] != p["source_item_version"]
        or current != p["before"]
        or source["data"] != current["data"]
        or any(source[k] != current[k] for k in ("library_type", "library_id", "key"))
    ):
        raise Error("original/fresh source binding mismatch")
    return _hash({"original": original, "fresh": fresh, "captured_at": captured_at})
