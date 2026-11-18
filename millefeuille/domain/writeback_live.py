"""Root custody binding, distinct from caller-supplied core projections."""

import hashlib
from pathlib import Path

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ApprovedLiveRequest,
    LiveDisposalPolicy,
    LiveSelector,
    LiveTarget,
    validate_approved_live_receipt_for_no_effect,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_core import _Authority, _plan
from millefeuille.domain.writeback_live_files import checked_file, decode

EVIDENCE_ROOT = Path("/var/lib/millefeuille/writeback-evidence-v1")
OUTPUT_ROOT = Path("/var/lib/millefeuille/writeback-v1")
STOPS = ("auth-failure", "schema-failure", "source-drift")


def prepare(inputs):
    """Pure no-auth planner for offline operator review only."""
    if set(inputs) != {
        "materialized",
        "before",
        "collection",
        "add_tags",
        "remove_tags",
    }:
        raise Error("exact writeback inputs required")
    return _plan(**inputs)


def load_evidence(config):
    """Only configured root-custodied files, never caller-selected source paths.

    Administrator attestation must come from independently accepted materialization,
    not the proposal publisher. Original source and fresh snapshot stay distinct.
    """
    descriptors = config["files"]
    names = {
        "index",
        "manifest",
        "decision",
        "taxonomy_lock",
        "source",
        "acceptance",
        "original_parent",
        "original_proof",
        "classification_ledger",
        "classification_chain",
        "classification_output",
        "before",
        "collection",
        "source_bytes",
    }
    if set(descriptors) != names:
        raise Error("complete evidence custody required")
    raw = {
        name: checked_file(EVIDENCE_ROOT, desc) for name, desc in descriptors.items()
    }
    docs = {
        name: decode(value)
        for name, value in raw.items()
        if name not in ("source_bytes", "classification_ledger")
    }
    from millefeuille.domain.writeback_live_chain import validate_chain

    validate_chain(
        raw["classification_ledger"],
        docs["classification_chain"],
        run_id=docs["decision"]["run_id"],
        paper_id=docs["decision"]["paper_id"],
        published=docs["classification_output"],
    )
    materialized = {
        name: docs[name]
        for name in (
            "index",
            "manifest",
            "decision",
            "taxonomy_lock",
            "source",
            "acceptance",
        )
    }
    if (
        "sha256:" + hashlib.sha256(raw["source_bytes"]).hexdigest()
        != materialized["source"]["source_hash"]
    ):
        raise Error("original source bytes mismatch")
    from millefeuille.domain.writeback_core import _snapshot
    from millefeuille.domain.writeback_http import _checked_observation

    original, before = docs["original_parent"], docs["before"]
    proven, _ = _checked_observation(docs["original_proof"])
    if proven != _snapshot(original):
        raise Error("original source raw proof mismatch")
    if (
        original["version"] != materialized["source"]["source_item_version"]
        or any(original[k] != before[k] for k in ("library_type", "library_id", "key"))
        or original["data"] != before["data"]
    ):
        raise Error("original parent content drift")
    inputs = {
        "materialized": materialized,
        "before": before,
        "collection": docs["collection"],
        "add_tags": config["add_tags"],
        "remove_tags": config["remove_tags"],
    }
    plan = prepare(inputs)
    expected = {k: before[k] for k in ("library_type", "library_id", "key")}
    expected["collection_key"] = docs["collection"]["key"]
    if config["allowlist"] != expected or config["accepted_plan_digest"] != plan.digest:
        raise Error("configured exact allowlist/accepted plan mismatch")
    return plan


def request_scope(plan, operation, audit=None):
    if operation not in ("execute", "rollback"):
        raise Error("writeback operation denied")
    if (operation == "rollback") != (audit is not None):
        raise Error("separate rollback audit binding required")
    p = plan.value()
    targets = [
        LiveTarget("writeback-plan-v1", plan.digest),
        LiveTarget("zotero-item-key", p["before"]["key"]),
        LiveTarget(
            "zotero-library",
            p["before"]["library_type"] + ":" + p["before"]["library_id"],
        ),
        LiveTarget("zotero-collection", p["mapping"]["key"]),
    ]
    if audit is not None:
        targets.append(LiveTarget("writeback-execution-audit", audit))
    return ApprovedLiveRequest(
        run_id=p["run_id"],
        operations=("zotero.writeback." + operation,),
        targets=tuple(sorted(targets)),
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


def preflight(plan, operation, receipt_payload, audit=None):
    receipt = ApprovedLiveReceipt.from_dict(receipt_payload)
    if receipt.approval.approver_id != "fr-meyer":
        raise Error("writeback approver mismatch")
    validate_approved_live_receipt_for_no_effect(
        receipt, request_scope(plan, operation, audit)
    )
    return receipt


def authority(plan, operation, supplied, administrator, audit=None):
    """Exact root-owned receipt authenticates; core atomically consumes next.

    Non-consuming validation is not standalone execution permission.
    """
    if supplied != administrator:
        raise Error("receipt not authenticated by administrator")
    receipt = preflight(plan, operation, supplied, audit)
    return _Authority(
        receipt.receipt_id,
        receipt.content_digest,
        plan.digest,
        operation,
        receipt.expires_at,
        audit,
    )
