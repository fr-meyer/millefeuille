"""Root-owned one-use reservation for an exact GPT card output write.

This primitive records authorization before a future writer publishes files.
It must be invoked inside a privileged broker, never from the model process.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from millefeuille.domain.paper_card_live_execution import TrustedGptCardOutcome
from millefeuille.domain.paper_card_output_trusted_approval import (
    validate_trusted_gpt_card_output_write_approval,
)
from millefeuille.domain.paper_card_output_write_scope import GptCardOutputWritePreview
from millefeuille.domain.paper_card_publication_bundle import (
    GptCardPublicationBundle,
    plan_gpt_card_publication_bundle,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity

_CONTROL_DIR = Path("/etc/millefeuille")
_CONTROL_OWNER_UID = 0
_LEDGER_NAME = "gpt-card-write.sqlite3"
_PUBLICATION_TABLE = "publication_attempts"


def reserve_trusted_gpt_card_output_write_receipt(
    *,
    outcome: TrustedGptCardOutcome,
    publication: GptSummaryPublicationIdentity,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
    publication_bundle: GptCardPublicationBundle | None = None,
) -> GptCardOutputWritePreview:
    """Replan, verify root approval, and durably consume one write receipt."""

    if sys.platform != "linux" or os.geteuid() != 0:
        raise MillefeuilleContractError(
            "GPT card write reservation requires Linux root"
        )
    _require_control_dir()
    preview = validate_trusted_gpt_card_output_write_approval(
        outcome=outcome,
        publication=publication,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
        now=now,
    )
    if publication_bundle is not None:
        fresh_bundle = plan_gpt_card_publication_bundle(
            outcome=outcome,
            publication=publication,
            route_evidence_path=route_evidence_path,
            structure_evidence_path=structure_evidence_path,
            preparation_path=preparation_path,
            source_pack_root=source_pack_root,
            packet=packet,
            receipt=receipt,
        )
        if fresh_bundle != publication_bundle or publication_bundle.preview != preview:
            raise MillefeuilleContractError(
                "GPT card publication bundle changed before reservation"
            )
    database = _CONTROL_DIR / _LEDGER_NAME
    _require_private_database_or_absent(database)
    if not hasattr(os, "O_NOFOLLOW"):
        raise MillefeuilleContractError(
            "GPT card write ledger requires no-follow support"
        )
    try:
        descriptor = os.open(
            database, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        _require_private_database_or_absent(database)
    except OSError as exc:
        raise MillefeuilleContractError("GPT card write ledger is unavailable") from exc
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
        if publication_bundle is not None:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {_PUBLICATION_TABLE} ("
                "receipt_digest TEXT PRIMARY KEY, "
                "receipt_id TEXT UNIQUE NOT NULL, "
                "run_id TEXT NOT NULL, "
                "bundle_manifest_sha256 TEXT NOT NULL, "
                "file_count INTEGER NOT NULL, "
                "total_bytes INTEGER NOT NULL, "
                "status TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT audit_json FROM approvals").fetchall()
        replay_state = ReceiptReplayState.from_audit_records(
            [_parse_canonical_audit(row[0]) for row in rows]
        )
        request = packet.to_approved_live_request()
        validate_approved_live_receipt(
            receipt, request, now=now, replay_state=replay_state
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
        if publication_bundle is not None:
            _insert_publication_attempt(
                connection, receipt=receipt, bundle=publication_bundle
            )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return preview


def _insert_publication_attempt(
    connection: sqlite3.Connection,
    *,
    receipt: ApprovedLiveReceipt,
    bundle: GptCardPublicationBundle,
) -> None:
    connection.execute(
        f"INSERT INTO {_PUBLICATION_TABLE} "
        "(receipt_digest, receipt_id, run_id, bundle_manifest_sha256, "
        "file_count, total_bytes, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            receipt.content_digest,
            receipt.receipt_id,
            bundle.preview.run_id,
            bundle.bundle_manifest_sha256,
            len(bundle.files),
            bundle.total_bytes,
            "committing",
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )


def _require_control_dir() -> None:
    try:
        detail = _CONTROL_DIR.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            "GPT card write administrator control directory is missing"
        ) from exc
    if (
        not stat.S_ISDIR(detail.st_mode)
        or detail.st_uid != _CONTROL_OWNER_UID
        or stat.S_IMODE(detail.st_mode) & 0o022
    ):
        raise MillefeuilleContractError(
            "GPT card write control directory is not administrator-owned"
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
            "GPT card write replay database is not administrator-owned"
        )


def _parse_canonical_audit(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "GPT card write replay ledger is invalid"
        ) from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != value:
        raise MillefeuilleContractError("GPT card write replay ledger is not canonical")
    return payload


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
