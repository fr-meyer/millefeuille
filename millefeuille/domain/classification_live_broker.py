"""Root-only v1 route: distinct classify reservation and exact proposal publication.

Nothing provisions trust. Fixed root-owned approval files, peer credentials and a
new durable ledger are required. Summary/card authority is never accepted.
"""

from contextlib import suppress
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import sys
import time

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.classification_live import (
    ClassificationOutcome,
    output_identity,
    validated_files,
)
from millefeuille.domain.classification_live_scope import (
    OUTPUT_ROOT,
    canonical,
    digest,
    validate_scope,
)
from millefeuille.domain.exclusive_publication import CONTRACT, publish_snapshot
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ReceiptReplayState,
    build_approved_live_audit_record,
    validate_approved_live_receipt,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.paper_card_replay_reservation import (
    _require_control_dir,
    _require_private_database_or_absent,
)
from millefeuille.domain.paper_card_reservation_broker import (
    _require_peer_uid,
)
from millefeuille.domain.paper_card_trusted_approval import (
    _read_administrator_file,
    _reject_json_constant,
    _unique_json_object,
)

_ROOT_UID = 0
CONTROL_DIR = Path("/etc/millefeuille")
SOCKET = Path("/etc/millefeuille/gpt-classification-v1.sock")
LEDGER = Path("/etc/millefeuille/gpt-classification-v1.sqlite3")
APPROVALS = {
    "model.classify": Path("/etc/millefeuille/gpt-classification-v1-execution.json"),
    "classification.review": Path(
        "/etc/millefeuille/gpt-classification-v1-publication.json"
    ),
}
LIMIT = 1024 * 1024
EXCHANGE_SECONDS = 30.0


def _remaining(channel, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("classification exchange deadline")
    channel.settimeout(remaining)


def _read_exact(channel, size, deadline=None):
    # Production passes one deadline shared by ALL exchange phases.
    if deadline is None:
        timeout = channel.gettimeout()
        deadline = time.monotonic() + (EXCHANGE_SECONDS if timeout is None else timeout)
    chunks = bytearray()
    while len(chunks) < size:
        _remaining(channel, deadline)
        chunk = channel.recv(size - len(chunks))
        if not chunk:
            raise Error("classification frame ended early")
        chunks.extend(chunk)
    _remaining(channel, deadline)
    return bytes(chunks)


def _send_frame(channel, wire, deadline):
    view = memoryview(struct.pack("!I", len(wire)) + wire)
    while view:
        _remaining(channel, deadline)
        sent = channel.send(view)
        if sent <= 0:
            raise Error("classification frame send ended early")
        view = view[sent:]
    _remaining(channel, deadline)


def _serve_connection(channel, expected_uid):
    deadline = time.monotonic() + EXCHANGE_SECONDS
    _remaining(channel, deadline)
    _require_peer_uid(channel, expected_uid)
    try:
        size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
        if size > LIMIT:
            raise Error("classification request too large")
        payload = decode(_read_exact(channel, size, deadline))
        _remaining(channel, deadline)
        response = _process(payload)
    except (Error, OSError, ValueError, TypeError, sqlite3.Error):
        response = {"status": "denied-or-unconfirmed"}
    # Expiry after processing is ambiguous, never a new reply budget or retry.
    _send_frame(channel, canonical(response), deadline)


def decode(wire):
    return json.loads(
        wire,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def approval_record(*, inputs, packet, receipt):
    """Offline issuance contract only: no file writes or authority creation."""
    return {
        "schema": "millefeuille-classification-root-approval/v1",
        "packet_digest": packet.content_digest,
        "receipt_digest": receipt.content_digest,
        "inputs_sha256": digest(canonical(inputs)),
        "operation": packet.operations[0],
        "output_root": str(OUTPUT_ROOT),
        "approver_id": receipt.approval.approver_id,
        "approved_at": receipt.approval.approved_at,
    }


def _require_root_record(inputs, packet, receipt):
    if len(packet.operations) != 1 or packet.operations[0] not in APPROVALS:
        raise Error("wrong classification operation")
    wire = _read_administrator_file(APPROVALS[packet.operations[0]])
    expected = approval_record(inputs=inputs, packet=packet, receipt=receipt)
    if decode(wire) != expected or wire != canonical(expected) + b"\n":
        raise Error("classification administrator approval mismatch")


def _connect_ledger():
    _require_control_dir()
    if LEDGER.parent != SOCKET.parent or LEDGER.parent != CONTROL_DIR:
        raise Error("classification ledger path drift")
    _require_private_database_or_absent(LEDGER)
    try:
        fd = os.open(
            LEDGER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        _require_private_database_or_absent(LEDGER)
    else:
        os.close(fd)
    connection = sqlite3.connect(LEDGER, timeout=15, isolation_level=None)
    _require_private_database_or_absent(LEDGER)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS approvals (receipt_id TEXT PRIMARY KEY, "
        "receipt_digest TEXT UNIQUE NOT NULL, plan_sha256 TEXT NOT NULL, "
        "operation TEXT NOT NULL, audit_json TEXT NOT NULL)"
    )
    return connection


def _consume(scope, packet, receipt, *, execution_digest=None):
    connection = _connect_ledger()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if execution_digest is not None:
            row = connection.execute(
                "SELECT plan_sha256,operation FROM approvals WHERE receipt_digest=?",
                (execution_digest,),
            ).fetchone()
            if (
                row != (scope.plan_sha256, "model.classify")
                or execution_digest == scope.receipt_digest
            ):
                raise Error(
                    "publication lacks matching consumed classification execution"
                )
        audits = [
            decode(row[0])
            for row in connection.execute("SELECT audit_json FROM approvals")
        ]
        replay = ReceiptReplayState.from_audit_records(audits)
        request = packet.to_approved_live_request()
        validate_approved_live_receipt(receipt, request, replay_state=replay)
        audit = build_approved_live_audit_record(
            receipt, request, status="consumed", replay_state=replay
        )
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?)",
            (
                receipt.receipt_id,
                receipt.content_digest,
                scope.plan_sha256,
                scope.operation,
                canonical(audit).decode(),
            ),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _process(payload):
    if sys.platform != "linux" or os.geteuid() != _ROOT_UID:
        raise Error("classification broker requires Linux root")
    if not isinstance(payload, dict) or set(payload) != {
        "inputs",
        "packet",
        "receipt",
        "outcome",
    }:
        raise Error("classification wire fields")
    inputs = payload["inputs"]
    packet = OperatorPreflightPacket.from_dict(payload["packet"])
    receipt = ApprovedLiveReceipt.from_dict(payload["receipt"])
    # Authenticate exact paths/receipt before privileged source reads.
    _require_root_record(inputs, packet, receipt)
    outcome_wire = payload["outcome"]
    if outcome_wire is None:
        scope = validate_scope(inputs=inputs, packet=packet, receipt=receipt)
        _consume(scope, packet, receipt)
        return {"status": "reserved", "scope": asdict(scope)}
    if (
        not isinstance(outcome_wire, dict)
        or set(outcome_wire)
        != {"execution_receipt_digest", "plan_sha256", "result", "output"}
        or not isinstance(outcome_wire["output"], str)
    ):
        raise Error("classification outcome fields")
    outcome = ClassificationOutcome(
        outcome_wire["execution_receipt_digest"],
        outcome_wire["plan_sha256"],
        OpenClawModelExecution(
            result=outcome_wire["result"], output=outcome_wire["output"].encode()
        ),
    )
    files = validated_files(inputs=inputs, outcome=outcome)
    scope = validate_scope(
        inputs=inputs,
        packet=packet,
        receipt=receipt,
        output_sha256=output_identity(files),
    )
    exact = plan_exact_outputs(root=OUTPUT_ROOT, outputs=files)
    if any(item.before_bytes is not None for item in exact):
        raise Error("classification output already exists")
    _consume(scope, packet, receipt, execution_digest=outcome.execution_receipt_digest)
    # Revalidate after consuming authority, immediately before durable effects.
    if validated_files(inputs=inputs, outcome=outcome) != files:
        raise Error("classification changed before publication")
    publish_snapshot(
        root=OUTPUT_ROOT,
        outputs=exact,
        approved_refs=tuple(x.ref for x in exact),
        transaction_ref=".classification-" + scope.receipt_digest.split(":")[1],
        namespace_contract=CONTRACT,
        max_files=3,
        max_bytes=LIMIT,
        max_aux_paths=8,
        max_aux_bytes=4 * LIMIT,
        max_journal_bytes=LIMIT,
    )
    return {"status": "published-proposal-not-classified", "scope": asdict(scope)}


def request(*, inputs, packet, receipt, outcome=None):
    files = None if outcome is None else validated_files(inputs=inputs, outcome=outcome)
    scope = validate_scope(
        inputs=inputs,
        packet=packet,
        receipt=receipt,
        output_sha256=None if files is None else output_identity(files),
    )
    parent, detail = SOCKET.parent.lstat(), SOCKET.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISSOCK(detail.st_mode)
        or parent.st_uid != 0
        or detail.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise Error("classification socket is not root-owned")
    wire = canonical(
        {
            "inputs": inputs,
            "packet": packet.to_dict(),
            "receipt": receipt.to_dict(),
            "outcome": None
            if outcome is None
            else {
                "execution_receipt_digest": outcome.execution_receipt_digest,
                "plan_sha256": outcome.plan_sha256,
                "result": outcome.execution.result,
                "output": outcome.execution.output.decode(),
            },
        }
    )
    if len(wire) > LIMIT:
        raise Error("classification wire too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        deadline = time.monotonic() + EXCHANGE_SECONDS
        _remaining(channel, deadline)
        channel.connect(str(SOCKET))
        _require_peer_uid(channel, 0)
        _send_frame(channel, wire, deadline)
        size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
        if size > 4096:
            raise Error("classification response too large")
        reply = decode(_read_exact(channel, size, deadline))
    expected = {
        "status": "reserved"
        if outcome is None
        else "published-proposal-not-classified",
        "scope": asdict(scope),
    }
    if reply != expected:
        raise Error("classification broker refused or outcome ambiguous")
    return reply


def serve_one():
    if (
        sys.platform != "linux"
        or os.geteuid() != 0
        or not hasattr(socket, "SO_PEERCRED")
    ):
        raise Error("classification broker requires Linux root peer credentials")
    _require_control_dir()
    if SOCKET.parent != CONTROL_DIR or SOCKET.exists() or SOCKET.is_symlink():
        raise Error("classification socket unavailable")
    import pwd

    user = pwd.getpwnam("node")
    bound = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            mask = os.umask(0o777)
            try:
                listener.bind(str(SOCKET))
                bound = SOCKET.lstat()
            finally:
                os.umask(mask)
            listener.listen(1)
            os.chown(SOCKET, 0, user.pw_gid)
            os.chmod(SOCKET, 0o660)
            listener.settimeout(120)
            with listener.accept()[0] as channel:
                _serve_connection(channel, user.pw_uid)
    finally:
        if bound is not None:
            with suppress(FileNotFoundError):
                current = SOCKET.lstat()
                if (current.st_dev, current.st_ino) == (bound.st_dev, bound.st_ino):
                    SOCKET.unlink()


if __name__ == "__main__":
    serve_one()
