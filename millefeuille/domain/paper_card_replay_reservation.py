"""Privileged durable reservation for one approved GPT card receipt.

This is the ledger primitive for a future root-owned broker. It does not
dispatch a provider call and must never run in the unprivileged model process.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any

from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ReceiptReplayState,
    build_approved_live_audit_record,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.paper_card_execution_scope import GptCardApprovalPreview
from millefeuille.domain.paper_card_trusted_approval import (
    validate_trusted_gpt_card_approval,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity

_CONTROL_DIR = Path("/etc/millefeuille")
_CONTROL_OWNER_UID = 0
_LEDGER_NAME = "gpt-card.sqlite3"


def reserve_trusted_gpt_card_receipt(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    artifact_root: str | Path,
    source_pack_root: str | Path,
    publication: GptSummaryPublicationIdentity,
    run_id: str,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> GptCardApprovalPreview:
    """Replan, verify administrator approval, then consume the receipt once.

    A caller must run in a privileged broker. The result only confirms
    reservation; it is not a model execution or durable paper write.
    """

    if sys.platform != "linux" or os.geteuid() != 0:
        raise MillefeuilleContractError("GPT card reservation requires Linux root")
    _require_control_dir()
    preview = validate_trusted_gpt_card_approval(
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        artifact_root=artifact_root,
        source_pack_root=source_pack_root,
        publication=publication,
        run_id=run_id,
        packet=packet,
        receipt=receipt,
        now=now,
    )
    database = _CONTROL_DIR / _LEDGER_NAME
    _require_private_database_or_absent(database)
    if not hasattr(os, "O_NOFOLLOW"):
        raise MillefeuilleContractError("GPT card ledger requires no-follow support")
    try:
        descriptor = os.open(
            database,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        _require_private_database_or_absent(database)
    except OSError as exc:
        raise MillefeuilleContractError("GPT card ledger is unavailable") from exc
    else:
        os.close(descriptor)
    connection = sqlite3.connect(database, timeout=15, isolation_level=None)
    try:
        _require_private_database_or_absent(database)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "receipt_id TEXT PRIMARY KEY, "
            "receipt_digest TEXT UNIQUE NOT NULL, "
            "audit_json TEXT NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT audit_json FROM approvals").fetchall()
        replay_state = ReceiptReplayState.from_audit_records(
            [_parse_canonical_audit(row[0]) for row in rows]
        )
        request = packet.to_approved_live_request()
        validate_approved_live_receipt(
            receipt,
            request,
            now=now,
            replay_state=replay_state,
        )
        audit = build_approved_live_audit_record(
            receipt,
            request,
            evaluated_at=now,
            status="consumed",
            replay_state=replay_state,
        )
        connection.execute(
            "INSERT INTO approvals (receipt_id, receipt_digest, audit_json) "
            "VALUES (?, ?, ?)",
            (receipt.receipt_id, receipt.content_digest, _canonical_json(audit)),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return preview


def _require_control_dir() -> None:
    try:
        detail = _CONTROL_DIR.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            "GPT card administrator control directory is missing"
        ) from exc
    if (
        not stat.S_ISDIR(detail.st_mode)
        or detail.st_uid != _CONTROL_OWNER_UID
        or stat.S_IMODE(detail.st_mode) & 0o022
    ):
        raise MillefeuilleContractError(
            "GPT card control directory is not administrator-owned"
        )


def _require_private_database_or_absent(path: Path) -> None:
    try:
        detail = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(detail.st_mode)
        or detail.st_uid != _CONTROL_OWNER_UID
        or stat.S_IMODE(detail.st_mode) != 0o600
    ):
        raise MillefeuilleContractError(
            "GPT card replay database is not administrator-owned"
        )


def _parse_canonical_audit(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError("GPT card replay ledger is invalid") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != value:
        raise MillefeuilleContractError("GPT card replay ledger is not canonical")
    return payload


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
