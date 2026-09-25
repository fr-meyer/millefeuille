"""One-shot root broker for an exact GPT summary receipt reservation.

The unprivileged process can request one reservation over a fixed Unix socket.
The broker rechecks the prepared source, trusted approval, and replay ledger.
Neither side dispatches a provider call in this module.
"""

from __future__ import annotations

from contextlib import suppress
import hmac
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time
from typing import Any

from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.summary_execution_scope import (
    SummaryApprovalPreview,
    validate_gpt_summary_approval_preview,
)
from millefeuille.domain.summary_replay_reservation import (
    _CONTROL_DIR,
    _require_control_dir,
    reserve_trusted_gpt_summary_receipt,
)

_BROKER_SOCKET_PATH = Path("/etc/millefeuille/gpt-summary.sock")
_BROKER_OWNER_UID = 0
_REQUEST_LIMIT = 131072
_REPLY_LIMIT = 4096
_EVIDENCE_FIELDS = frozenset(
    {"route_evidence_path", "structure_evidence_path", "preparation_path"}
)


def request_gpt_summary_reservation(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    artifact_root: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
) -> SummaryApprovalPreview:
    """Ask the fixed root broker to consume the exact approved receipt once."""

    evidence = {
        "route_evidence_path": str(route_evidence_path),
        "structure_evidence_path": str(structure_evidence_path),
        "preparation_path": str(preparation_path),
    }
    _require_evidence_paths(evidence, str(source_pack_root))
    preview = validate_gpt_summary_approval_preview(
        **evidence,
        artifact_root=artifact_root,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
    )
    path = _BROKER_SOCKET_PATH
    try:
        parent = path.parent.lstat()
        detail = path.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError("GPT summary broker is unavailable") from exc
    if (
        os.name != "posix"
        or not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISSOCK(detail.st_mode)
        or parent.st_uid != _BROKER_OWNER_UID
        or detail.st_uid != _BROKER_OWNER_UID
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise MillefeuilleContractError("GPT summary broker is not administrator-owned")
    request_bytes = _canonical_json(
        {"packet": packet.to_dict(), "receipt": receipt.to_dict(), "evidence": evidence}
    ).encode("utf-8")
    if len(request_bytes) > _REQUEST_LIMIT:
        raise MillefeuilleContractError("GPT summary broker request is too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(15)
            channel.connect(str(path))
            _require_peer_uid(channel, _BROKER_OWNER_UID)
            channel.sendall(struct.pack("!I", len(request_bytes)) + request_bytes)
            size = struct.unpack("!I", _read_exact(channel, 4))[0]
            if size > _REPLY_LIMIT:
                raise MillefeuilleContractError("GPT summary broker reply is too large")
            reply = json.loads(
                _read_exact(channel, size).decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
    except (OSError, UnicodeError, ValueError) as exc:
        raise MillefeuilleContractError("GPT summary broker failed closed") from exc
    if (
        not isinstance(reply, dict)
        or set(reply)
        != {"status", "receipt_digest", "manifest_sha256", "request_count"}
        or reply["status"] != "reserved"
        or not _digest_matches(reply["receipt_digest"], preview.receipt_digest)
        or not _digest_matches(reply["manifest_sha256"], preview.manifest_sha256)
        or type(reply["request_count"]) is not int
        or reply["request_count"] != preview.request_count
    ):
        raise MillefeuilleContractError("GPT summary broker refused reservation")
    return preview


def serve_one_reservation(*, timeout_seconds: int = 120) -> None:
    """Serve one node-user request, commit its replay record, then exit."""

    if (
        sys.platform != "linux"
        or os.geteuid() != 0
        or not hasattr(socket, "SO_PEERCRED")
    ):
        raise MillefeuilleContractError("GPT summary broker requires Linux root")
    _require_control_dir()
    if _BROKER_SOCKET_PATH.parent != _CONTROL_DIR:
        raise MillefeuilleContractError("GPT summary broker socket path drifted")
    if _BROKER_SOCKET_PATH.exists() or _BROKER_SOCKET_PATH.is_symlink():
        raise MillefeuilleContractError("GPT summary broker socket already exists")
    import pwd

    model_user = pwd.getpwnam("node")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            # Keep the new pathname inaccessible until listen() has completed.
            # Callers use its permissions as the readiness signal.
            previous_umask = os.umask(0o777)
            try:
                listener.bind(str(_BROKER_SOCKET_PATH))
            finally:
                os.umask(previous_umask)
            listener.listen(1)
            os.chown(_BROKER_SOCKET_PATH, 0, model_user.pw_gid)
            os.chmod(_BROKER_SOCKET_PATH, 0o660)
            deadline = time.monotonic() + timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("GPT summary broker listener timed out")
                listener.settimeout(remaining)
                channel, _ = listener.accept()
                try:
                    _require_peer_uid(channel, model_user.pw_uid)
                except (MillefeuilleContractError, OSError):
                    channel.close()
                    continue
                break
            with channel:
                channel.settimeout(15)
                try:
                    size = struct.unpack("!I", _read_exact(channel, 4))[0]
                    if size > _REQUEST_LIMIT:
                        raise MillefeuilleContractError(
                            "GPT summary broker request is too large"
                        )
                    payload = json.loads(
                        _read_exact(channel, size).decode("utf-8"),
                        object_pairs_hook=_unique_json_object,
                        parse_constant=_reject_json_constant,
                    )
                    response = _process_request(payload)
                except (MillefeuilleContractError, OSError, UnicodeError, ValueError):
                    response = {
                        "status": "denied",
                        "receipt_digest": "",
                        "manifest_sha256": "",
                        "request_count": 0,
                    }
                encoded = _canonical_json(response).encode("utf-8")
                channel.sendall(struct.pack("!I", len(encoded)) + encoded)
    finally:
        with suppress(FileNotFoundError):
            _BROKER_SOCKET_PATH.unlink()


def _process_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "packet",
        "receipt",
        "evidence",
    }:
        raise MillefeuilleContractError("GPT summary broker request is invalid")
    evidence = payload["evidence"]
    if (
        not isinstance(evidence, dict)
        or set(evidence) != _EVIDENCE_FIELDS
        or any(not isinstance(value, str) for value in evidence.values())
    ):
        raise MillefeuilleContractError("GPT summary evidence paths are invalid")
    packet = OperatorPreflightPacket.from_dict(payload["packet"])
    receipt = ApprovedLiveReceipt.from_dict(payload["receipt"])
    _require_evidence_paths(evidence, packet.source_pack_root)
    preview = reserve_trusted_gpt_summary_receipt(
        **evidence,
        artifact_root=packet.artifact_root,
        source_pack_root=packet.source_pack_root,
        packet=packet,
        receipt=receipt,
    )
    return {
        "status": "reserved",
        "receipt_digest": preview.receipt_digest,
        "manifest_sha256": preview.manifest_sha256,
        "request_count": preview.request_count,
    }


def _require_evidence_paths(evidence: dict[str, str], source_pack_root: str) -> None:
    root = Path(source_pack_root)
    if not root.is_absolute() or os.path.normpath(source_pack_root) != source_pack_root:
        raise MillefeuilleContractError("GPT summary source root is invalid")
    for value in evidence.values():
        path = Path(value)
        if not path.is_absolute() or os.path.normpath(value) != value:
            raise MillefeuilleContractError("GPT summary evidence path is invalid")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise MillefeuilleContractError(
                "GPT summary evidence path is outside its source root"
            ) from exc
        if not relative.parts:
            raise MillefeuilleContractError("GPT summary evidence path is invalid")
        # This preflight read follows no path component. The preparation
        # verifier repeats no-follow reads during the privileged replan, so a
        # later pathname swap cannot turn this check into authorization.
        read_bytes_no_follow(path, "GPT summary evidence")


def _require_peer_uid(channel: socket.socket, expected_uid: int) -> None:
    if not hasattr(socket, "SO_PEERCRED"):
        raise MillefeuilleContractError("GPT summary broker needs peer credentials")
    identity = channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", identity)
    if uid != expected_uid:
        raise MillefeuilleContractError("GPT summary broker peer identity mismatch")


def _read_exact(channel: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = channel.recv(size - len(result))
        if not part:
            raise OSError("GPT summary broker closed early")
        result.extend(part)
    return bytes(result)


def _digest_matches(value: Any, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)


def _unique_json_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate broker field")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-standard JSON constant")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    serve_one_reservation()
