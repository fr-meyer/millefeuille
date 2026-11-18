"""Independently authorized preparation reads, before execute consumption.

Caller must provide already authenticated administrator receipt, never a digest
claim. This source function is not yet exposed by service until import is complete.
"""

from datetime import UTC, datetime
import time

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
from millefeuille.domain.writeback_http import _HTTPReader
from millefeuille.domain.writeback_live import EVIDENCE_ROOT, OUTPUT_ROOT, STOPS
from millefeuille.domain.writeback_live_collection import (
    CollectionReader,
    checked_collection,
)
from millefeuille.domain.writeback_live_science import validate_source_observations


def preparation_request(plan, expected_collection, execution_audit=None):
    from millefeuille.domain.writeback_core import _wire
    from millefeuille.domain.writeback_live_collection import project_collection

    identity = {
        k: expected_collection[k] for k in ("library_type", "library_id", "key")
    }
    data = {
        k: expected_collection[k]
        for k in ("key", "version", "name", "parentCollection")
    }
    raw = _wire(
        {
            "key": identity["key"],
            "version": data["version"],
            "library": {
                "type": identity["library_type"],
                "id": int(identity["library_id"]),
            },
            "data": data,
        }
    )
    snapshot, _ = project_collection(identity, raw, data["version"])
    if snapshot != expected_collection:
        raise Error("exact collection expectation required")
    return ApprovedLiveRequest(
        run_id=plan.value()["run_id"],
        operations=("zotero.writeback.prepare-read",),
        targets=tuple(
            sorted(
                (
                    LiveTarget("writeback-plan-v1", plan.digest),
                    LiveTarget("collection-expectation", _hash(expected_collection)),
                    LiveTarget("read-budget", "one-item-one-collection"),
                    LiveTarget(
                        "preparation-purpose",
                        "execute"
                        if execution_audit is None
                        else _hash(execution_audit),
                    ),
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


def prepare_remote(
    *,
    plan,
    expected_collection,
    original,
    receipt_payload,
    authenticated_receipt,
    journal,
    secret,
    transport=None,
    execution_audit=None,
):
    if receipt_payload != authenticated_receipt:
        raise Error("preparation receipt not authenticated")
    receipt = ApprovedLiveReceipt.from_dict(receipt_payload)
    if receipt.approval.approver_id != "fr-meyer":
        raise Error("preparation approver mismatch")
    validate_approved_live_receipt_for_no_effect(
        receipt, preparation_request(plan, expected_collection, execution_audit)
    )
    p = plan.value()
    if any(
        expected_collection[k] != p["mapping"][k]
        for k in ("library_type", "library_id", "key")
    ):
        raise Error("collection expectation differs from immutable mapping")
    if (
        execution_audit is not None
        and journal.execution(plan, _hash(execution_audit)) != execution_audit
    ):
        raise Error("rollback preparation requires owned audit")
    # Use the same externally committed protected ledger and uniqueness constraints,
    # but never manufacture execute/rollback authority for this independent read.
    from millefeuille.domain.writeback_core_journal import _Journal

    if type(journal) is not _Journal:
        raise Error("protected journal required")
    db = journal._db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO receipts VALUES (?,?,?,?)",
            (
                receipt.receipt_id,
                receipt.content_digest,
                plan.digest,
                "prepare-read" if execution_audit is None else "prepare-rollback",
            ),
        )
        journal._commit(db)
    finally:
        db.close()
    journal._sync()
    observations = _bounded_observations(
        plan, expected_collection, original, secret, transport, execution_audit
    )
    from millefeuille.domain.writeback_core import _wire

    db = journal._db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO events(plan,receipt,kind,payload) VALUES (?,?,?,?)",
            (
                plan.digest,
                receipt.content_digest,
                "preparation-result",
                _wire(observations).decode("utf-8"),
            ),
        )
        journal._commit(db)
    finally:
        db.close()
    journal._sync()
    return observations


def _bounded_observations(
    plan, expected_collection, original, secret, transport=None, execution_audit=None
):
    """Internal only; admitted caller must consume preparation receipt first."""
    deadline = time.monotonic() + 5
    identity = {
        k: plan.value()["before"][k] for k in ("library_type", "library_id", "key")
    }
    collection_identity = {k: expected_collection[k] for k in identity}
    observed_collection = CollectionReader(secret, transport).read_collection(
        collection_identity, deadline=deadline
    )
    checked_collection(observed_collection, expected_collection)
    fresh = _HTTPReader(secret, transport).read(identity, deadline=deadline)
    captured = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if execution_audit is None:
        digest = validate_source_observations(
            plan, original, fresh, captured_at=captured
        )
    else:
        from millefeuille.domain.writeback_http import _checked_observation

        current, _ = _checked_observation(fresh)
        if current != execution_audit["observed"]:
            raise Error("rollback postwrite version/content changed")
        digest = _hash({"fresh": fresh, "execution_audit": _hash(execution_audit)})
    return {
        "collection": observed_collection,
        "fresh": fresh,
        "captured_at": captured,
        "source_observations_digest": digest,
    }
