"""Root-only GPT summary publication from a validated transient handoff.

An administrator starts this boundary only after publishing the exact write
approval. A consumed receipt is never retried. The root ledger records a
pending attempt before any paper write and a terminal or uncertain outcome.
This is an internal API; it exposes no model-facing publication command.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import sys

from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.summary_execution_scope import SummaryApprovalPreview
from millefeuille.domain.summary_outcome_handoff import (
    decode_gpt_summary_outcome_handoff,
)
from millefeuille.domain.summary_output_replay_reservation import (
    _CONTROL_DIR,
    _LEDGER_NAME,
    _PUBLICATION_TABLE,
    _require_control_dir,
    _require_private_database_or_absent,
    reserve_trusted_gpt_summary_output_write_receipt,
)
from millefeuille.domain.summary_publication_bundle import (
    plan_gpt_summary_publication_bundle,
)
from millefeuille.domain.summary_publication_fs import (
    GptSummaryFilesystemCommit,
    GptSummaryPublicationError,
    commit_prevalidated_gpt_summary_bundle,
)


class GptSummaryPublicationAuditUncertainError(MillefeuilleContractError):
    """The receipt is consumed and the terminal audit state is unknown."""


def publish_trusted_gpt_summary_handoff(
    *,
    encoded_outcome: bytes,
    execution_approval: SummaryApprovalPreview,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> GptSummaryFilesystemCommit:
    """Consume one exact write approval, then publish an audited whole run."""

    if sys.platform != "linux" or os.geteuid() != 0:
        raise MillefeuilleContractError("GPT publication broker requires Linux root")
    _require_control_dir()
    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    outcome = decode_gpt_summary_outcome_handoff(
        encoded_outcome, expected_approval=execution_approval, **evidence
    )
    scope = {
        "outcome": outcome,
        "run_id": run_id,
        "source_pack_root": source_pack_root,
        "packet": packet,
        "receipt": receipt,
        **evidence,
    }
    bundle = plan_gpt_summary_publication_bundle(**scope)
    reserved = reserve_trusted_gpt_summary_output_write_receipt(
        **scope, now=now, publication_bundle=bundle
    )
    if reserved != bundle.preview:
        raise MillefeuilleContractError("GPT publication reservation drift")
    try:
        fresh = plan_gpt_summary_publication_bundle(**scope)
        if fresh != bundle:
            raise MillefeuilleContractError("GPT publication source or output drift")
        committed = commit_prevalidated_gpt_summary_bundle(
            bundle=fresh, source_pack_root=source_pack_root
        )
    except Exception as exc:
        uncertain = isinstance(exc, GptSummaryPublicationError) and (
            exc.committed or not exc.cleanup_complete
        )
        try:
            _finish_attempt(
                receipt_digest=receipt.content_digest,
                status="uncertain" if uncertain else "failed-before-commit",
            )
        except Exception as audit_exc:
            raise GptSummaryPublicationAuditUncertainError(
                "GPT publication audit finalization failed; outcome uncertain"
            ) from audit_exc
        raise
    try:
        _finish_attempt(receipt_digest=receipt.content_digest, status="published")
    except Exception as exc:
        raise GptSummaryPublicationError(committed=True, cleanup_complete=True) from exc
    return committed


def _finish_attempt(*, receipt_digest: str, status: str) -> None:
    if status not in {"published", "failed-before-commit", "uncertain"}:
        raise MillefeuilleContractError("GPT publication audit status is invalid")
    connection = _open_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            f"UPDATE {_PUBLICATION_TABLE} SET status = ?, updated_at = ? "
            "WHERE receipt_digest = ? AND status = 'committing'",
            (status, _timestamp(), receipt_digest),
        )
        if changed.rowcount != 1:
            raise MillefeuilleContractError("GPT publication audit state drift")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _open_ledger() -> sqlite3.Connection:
    _require_control_dir()
    database = _CONTROL_DIR / _LEDGER_NAME
    if not database.exists():
        raise MillefeuilleContractError("GPT publication receipt ledger is missing")
    _require_private_database_or_absent(database)
    connection = sqlite3.connect(database, timeout=15, isolation_level=None)
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
