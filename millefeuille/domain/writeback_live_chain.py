"""Read-only classification reservation snapshot; never creates a ledger.

Snapshot is root-custodied and independently pinned by scientific acceptance's
exact files target. Rows must be genuine consumed audit records, not proposals.
"""

import sqlite3

from millefeuille.domain.live_receipts import ReceiptReplayState
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.writeback_live_files import decode


def validate_chain(raw, expected, *, run_id, paper_id, published):
    if set(expected) != {
        "execution_receipt_digest",
        "publication_receipt_digest",
        "classification_plan_sha256",
    }:
        raise Error("exact classification reservation identities required")
    if expected["execution_receipt_digest"] == expected["publication_receipt_digest"]:
        raise Error("distinct classification authorities required")
    from millefeuille.domain.classification_live import output_identity

    if (
        not isinstance(published, dict)
        or not 1 <= len(published) <= 3
        or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in published.items()
        )
    ):
        raise Error("exact published classification bytes required")
    output_digest = output_identity(
        {k: v.encode("utf-8") for k, v in published.items()}
    )
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(raw)
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            "SELECT receipt_digest,plan_sha256,operation,audit_json "
            "FROM approvals WHERE receipt_digest IN (?,?)",
            (
                expected["execution_receipt_digest"],
                expected["publication_receipt_digest"],
            ),
        ).fetchall()
    finally:
        db.close()
    if len(rows) != 2:
        raise Error("classification consumed chain missing")
    audits = []
    for digest, plan, operation, wire in rows:
        wanted = (
            "model.classify"
            if digest == expected["execution_receipt_digest"]
            else "classification.review"
        )
        if plan != expected["classification_plan_sha256"] or operation != wanted:
            raise Error("classification reservation chain mismatch")
        audit = decode(wire)
        if (
            audit.get("receipt_digest") != digest
            or audit.get("status") != "consumed"
            or audit.get("scope", {}).get("operations") != [wanted]
        ):
            raise Error("classification audit identity mismatch")
        targets = {(x["kind"], x["id"]) for x in audit["scope"]["targets"]}
        if audit["run_id"] != run_id or ("paper-id", paper_id) not in targets:
            raise Error("classification chain run/paper mismatch")
        if ("classification-plan-v1", plan) not in targets:
            raise Error("classification chain plan target mismatch")
        if (
            wanted == "classification.review"
            and ("classification-output-v1", output_digest) not in targets
        ):
            raise Error("published classification output mismatch")
        audits.append(audit)
    ReceiptReplayState.from_audit_records(audits)
    return expected
