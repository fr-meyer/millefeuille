"""Privileged one-shot approval and replay reservation for the GPT canary.

The broker is started by the GCP administrator only after human approval is
published in its root-owned control directory. It serves one node-user request,
atomically reserves its receipt in a root-owned ledger, and exits. The model
process never receives write access to approval or replay state.
"""

from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import sys
from typing import Any

from millefeuille.domain.gpt_oauth_canary import (
    _BROKER_SOCKET_PATH,
    _canonical_json,
    _read_exact,
    _reject_json_constant,
    _require_canary_scope,
    _unique_json_object,
    _verify_trusted_approval,
)
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ReceiptReplayState,
    build_approved_live_audit_record,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket

_CONTROL_DIR = Path("/etc/millefeuille")
_LEDGER_NAME = "gpt-oauth-canary.sqlite3"
_CONTROL_OWNER_UID = 0
_REQUEST_LIMIT = 131072


def serve_one_reservation(*, timeout_seconds: int = 120) -> None:
    """Listen once as root, reserve one exact receipt, and return an ack."""

    if (
        sys.platform != "linux"
        or os.geteuid() != 0
        or not hasattr(socket, "SO_PEERCRED")
    ):
        raise MillefeuilleContractError("GPT canary broker requires Linux root")
    _require_control_dir()
    if _BROKER_SOCKET_PATH.parent != _CONTROL_DIR:
        raise MillefeuilleContractError("GPT canary broker socket path drifted")
    if _BROKER_SOCKET_PATH.exists() or _BROKER_SOCKET_PATH.is_symlink():
        raise MillefeuilleContractError("GPT canary broker socket already exists")
    import pwd

    model_user = pwd.getpwnam("node")
    expected_client_uid = model_user.pw_uid
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(_BROKER_SOCKET_PATH))
            os.chown(_BROKER_SOCKET_PATH, 0, model_user.pw_gid)
            os.chmod(_BROKER_SOCKET_PATH, 0o660)
            listener.listen(1)
            listener.settimeout(timeout_seconds)
            with listener.accept()[0] as channel:
                channel.settimeout(15)
                credentials = channel.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, 12
                )
                _pid, client_uid, _gid = struct.unpack("3i", credentials)
                if client_uid != expected_client_uid:
                    raise MillefeuilleContractError(
                        "GPT canary broker peer is not the model user"
                    )
                try:
                    size = struct.unpack("!I", _read_exact(channel, 4))[0]
                    if size > _REQUEST_LIMIT:
                        raise MillefeuilleContractError(
                            "GPT canary broker request is too large"
                        )
                    payload = json.loads(
                        _read_exact(channel, size).decode("utf-8"),
                        object_pairs_hook=_unique_json_object,
                        parse_constant=_reject_json_constant,
                    )
                    if not isinstance(payload, dict) or set(payload) != {
                        "packet",
                        "receipt",
                    }:
                        raise MillefeuilleContractError(
                            "GPT canary broker request is invalid"
                        )
                    packet = OperatorPreflightPacket.from_dict(payload["packet"])
                    receipt = ApprovedLiveReceipt.from_dict(payload["receipt"])
                    _require_canary_scope(
                        packet, receipt, Path(packet.artifact_root)
                    )
                    _verify_trusted_approval(packet, receipt)
                    _reserve_root_approval(packet, receipt)
                    response = {
                        "status": "reserved",
                        "receipt_digest": receipt.content_digest,
                    }
                except (MillefeuilleContractError, OSError, UnicodeError, ValueError):
                    response = {"status": "denied", "receipt_digest": ""}
                encoded = _canonical_json(response).encode("utf-8")
                channel.sendall(struct.pack("!I", len(encoded)) + encoded)
    finally:
        with suppress(FileNotFoundError):
            _BROKER_SOCKET_PATH.unlink()


def _reserve_root_approval(
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
) -> None:
    """Commit replay evidence under root ownership before granting dispatch."""

    _require_control_dir()
    database = _CONTROL_DIR / _LEDGER_NAME
    _require_private_database_or_absent(database)
    try:
        descriptor = os.open(
            database,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        _require_private_database_or_absent(database)
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
        audits = [_parse_canonical_audit(row[0]) for row in rows]
        replay_state = ReceiptReplayState.from_audit_records(audits)
        request = packet.to_approved_live_request()
        validate_approved_live_receipt(
            receipt,
            request,
            replay_state=replay_state,
        )
        audit = build_approved_live_audit_record(
            receipt,
            request,
            status="consumed",
            replay_state=replay_state,
        )
        connection.execute(
            "INSERT INTO approvals (receipt_id, receipt_digest, audit_json) "
            "VALUES (?, ?, ?)",
            (
                receipt.receipt_id,
                receipt.content_digest,
                _canonical_json(audit),
            ),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _require_control_dir() -> None:
    try:
        detail = _CONTROL_DIR.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            "GPT canary administrator control directory is missing"
        ) from exc
    if (
        not stat.S_ISDIR(detail.st_mode)
        or detail.st_uid != _CONTROL_OWNER_UID
        or stat.S_IMODE(detail.st_mode) & 0o022
    ):
        raise MillefeuilleContractError(
            "GPT canary control directory is not administrator-owned"
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
            "GPT canary replay database is not administrator-owned"
        )


def _parse_canonical_audit(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError("GPT canary replay ledger is invalid") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != value:
        raise MillefeuilleContractError(
            "GPT canary replay ledger is not canonical"
        )
    return payload


if __name__ == "__main__":
    serve_one_reservation()
