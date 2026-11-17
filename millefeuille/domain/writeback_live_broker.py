"""Fixed one-shot main-thread writeback service. No runtime provisioning/fallback."""

import os
from pathlib import Path
import socket
import stat
import struct
import sys
import threading
import time

from millefeuille.domain.classification_live_broker import (
    _read_exact,
    _remaining,
    _send_frame,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.paper_card_reservation_broker import _require_peer_uid
from millefeuille.domain.writeback_core import _wire
from millefeuille.domain.writeback_core_journal import _Journal, _run
from millefeuille.domain.writeback_http import _HTTPReader, _HTTPWriter
from millefeuille.domain.writeback_live import OUTPUT_ROOT, authority, load_evidence
from millefeuille.domain.writeback_live_files import decode, protected_read

CONTROL = Path("/etc/millefeuille/writeback-v1")
SOCKET = CONTROL / "operator.sock"
CONFIG = CONTROL / "config.json"
COMMITMENT = CONTROL / "journal-commitment.json"
JOURNAL = OUTPUT_ROOT / "operations.sqlite3"
LIMIT = 131072


def _controls():
    config = decode(protected_read(CONFIG))
    if set(config) != {
        "schema",
        "caller_uid",
        "caller_gid",
        "files",
        "allowlist",
        "add_tags",
        "remove_tags",
        "accepted_plan_digest",
        "commitment_sha256",
        "transport_accepted",
        "collection",
    }:
        raise Error("exact root configuration required")
    if config["schema"] != "millefeuille-writeback-root/v1":
        raise Error("root contract version")
    # Must be set only after independent repaired-transport acceptance.
    if config["transport_accepted"] is not True:
        raise Error("transport acceptance absent")
    for field in ("caller_uid", "caller_gid"):
        if type(config[field]) is not int or config[field] < 0:
            raise Error("exact caller identity required")
    plan = load_evidence(config)
    _science(config, plan)
    from millefeuille.domain.writeback_live_prepare import preparation_request

    preparation_request(plan, config["collection"])
    return config


def _science(config, plan):
    from millefeuille.domain.writeback_core import _hash
    from millefeuille.domain.writeback_live_science import validate_acceptance

    receipt = decode(protected_read(CONTROL / "science-acceptance-receipt.json"))
    digest = validate_acceptance(plan, config["files"], receipt, receipt)
    attestation = decode(protected_read(CONTROL / "science-attestation.json"))
    if attestation != {
        "schema": "millefeuille-writeback-science-custody/v1",
        "plan_digest": plan.digest,
        "files_digest": _hash(config["files"]),
        "science_receipt_digest": digest,
    }:
        raise Error("science custody attestation mismatch")


def _root_main():
    return (
        sys.platform == "linux"
        and os.geteuid() == 0
        and threading.current_thread() is threading.main_thread()
    )


def process(payload, config, *, _observation=...):
    from millefeuille.domain import _failure_observations as observations

    token = observations.token() if _observation is ... else _observation
    observed = ["evidence", False]
    try:
        return _process(payload, config, observed)
    except Exception as exc:
        try:  # noqa: SIM105 - preserve explicit cancellation boundary
            observations.record(
                token,
                boundary="broker",
                stage=observed[0],
                reason="broker-denial",
                exception=exc,
                consumption=observed[1],
            )
        except Exception:
            pass
        raise


_OBSERVED_PROCESS = process


def _process(payload, config, observed):
    if not _root_main():
        raise Error("Linux root main-thread service required")
    if set(payload) != {"operation", "plan_digest", "receipt", "execution_audit"}:
        raise Error("exact request fields required")
    operation = payload["operation"]
    if operation not in ("execute", "rollback", "readback"):
        raise Error("unsupported operation")
    plan = load_evidence(config)
    _science(config, plan)
    if payload["plan_digest"] != plan.digest:
        raise Error("caller plan differs from root rederivation")
    import hashlib

    raw = protected_read(COMMITMENT)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != config["commitment_sha256"]:
        raise Error("original outside commitment drift")
    commitment = decode(raw)
    if commitment["path"] != str(JOURNAL):
        raise Error("journal path drift")
    observed[0] = "journal"
    journal = _Journal(JOURNAL, commitment)
    if operation == "readback":
        if payload["receipt"] is not None or payload["execution_audit"] is None:
            raise Error("readback requires exact owned execution audit only")
        return {
            "status": "journal-readback-not-remote",
            "audit": journal.execution(plan, payload["execution_audit"]),
        }
    administrator = decode(protected_read(CONTROL / (operation + "-receipt.json")))
    observed[0] = "authority"
    grant = authority(
        plan, operation, payload["receipt"], administrator, payload["execution_audit"]
    )
    # Secret only from fixed administrator-protected service file, never env/argv.
    secret = protected_read(CONTROL / "credential", limit=128).decode("ascii")
    from millefeuille.domain import writeback_live
    from millefeuille.domain.writeback_live_files import checked_file
    from millefeuille.domain.writeback_live_prepare import prepare_remote

    original = decode(
        checked_file(writeback_live.EVIDENCE_ROOT, config["files"]["original_proof"])
    )
    prep = decode(protected_read(CONTROL / (operation + "-preparation-receipt.json")))
    execution_audit = (
        None
        if operation == "execute"
        else journal.execution(plan, payload["execution_audit"])
    )
    observed[:] = ["preparation", None]
    prepare_remote(
        plan=plan,
        expected_collection=config["collection"],
        original=original,
        receipt_payload=prep,
        authenticated_receipt=prep,
        journal=journal,
        secret=secret,
        execution_audit=execution_audit,
    )
    writer, reader = _HTTPWriter(secret), _HTTPReader(secret)
    observed[:] = ["execute", True]
    return _run(
        plan=plan,
        authority=grant,
        journal=journal,
        writer=writer,
        reader=reader,
        rollback=operation == "rollback",
    )


def serve_connection(channel, config):
    deadline = time.monotonic() + 30
    _require_peer_uid(channel, config["caller_uid"])
    from millefeuille.domain import _failure_observations as observations

    observation = observations.token()
    entered_process = False
    try:
        size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
        if size > LIMIT:
            raise Error("request too large")
        payload = decode(_read_exact(channel, size, deadline))
        _remaining(channel, deadline)
        entered_process = True
        dispatch = process
        if dispatch is _OBSERVED_PROCESS:
            result = dispatch(payload, config, _observation=observation)
        else:
            result = dispatch(payload, config)
    except Exception as exc:
        if not entered_process:
            try:  # noqa: SIM105 - preserve explicit cancellation boundary
                observations.record(
                    observation,
                    boundary="broker",
                    stage="request",
                    reason="broker-denial",
                    exception=exc,
                    consumption=False,
                )
            except Exception:
                pass
        result = {"status": "denied-or-unconfirmed", "retry": False}
    raw = _wire(result)
    if len(raw) > 1048576:
        raise Error("response too large")
    _send_frame(channel, raw, deadline)


def request(payload):
    parent, node = SOCKET.parent.lstat(), SOCKET.lstat()
    if (
        parent.st_uid != 0
        or node.st_uid != 0
        or not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISSOCK(node.st_mode)
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise Error("untrusted root socket")
    raw = _wire(payload)
    if len(raw) > LIMIT:
        raise Error("request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        deadline = time.monotonic() + 30
        _remaining(channel, deadline)
        channel.connect(str(SOCKET))
        _require_peer_uid(channel, 0)
        _send_frame(channel, raw, deadline)
        size = struct.unpack("!I", _read_exact(channel, 4, deadline))[0]
        if size > 1048576:
            raise Error("response too large")
        return decode(_read_exact(channel, size, deadline))


def serve_one():
    if os.geteuid() != 0 or threading.current_thread() is not threading.main_thread():
        raise Error("root main thread required")
    config = _controls()
    bound = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            mask = os.umask(0o777)
            try:
                listener.bind(str(SOCKET))
                bound = SOCKET.lstat()
            finally:
                os.umask(mask)
            os.chown(SOCKET, 0, config["caller_gid"])
            os.chmod(SOCKET, 0o660)
            listener.listen(1)
            listener.settimeout(120)
            with listener.accept()[0] as channel:
                serve_connection(channel, config)
    finally:
        if bound is not None:
            current = SOCKET.lstat()
            if (current.st_dev, current.st_ino) == (bound.st_dev, bound.st_ino):
                SOCKET.unlink()


if __name__ == "__main__":
    serve_one()
