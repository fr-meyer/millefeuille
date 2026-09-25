"""One-shot local transport into the root GPT summary publication broker.

The model user sends a bounded transient handoff and approval metadata over a
fixed Unix socket. Only a root-started server can invoke the durable writer.
No private summary text is included in its reply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hmac
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
from typing import Any

from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.summary_execution_scope import SummaryApprovalPreview
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_outcome_handoff import (
    _MAX_BYTES as _HANDOFF_LIMIT,
)
from millefeuille.domain.summary_outcome_handoff import (
    encode_gpt_summary_outcome_handoff,
)
from millefeuille.domain.summary_output_replay_reservation import (
    _CONTROL_DIR,
    _require_control_dir,
)
from millefeuille.domain.summary_publication_broker import (
    publish_trusted_gpt_summary_handoff,
)
from millefeuille.domain.summary_publication_bundle import (
    plan_gpt_summary_publication_bundle,
)
from millefeuille.domain.summary_reservation_broker import (
    _canonical_json,
    _read_exact,
    _reject_json_constant,
    _require_evidence_paths,
    _require_peer_uid,
    _unique_json_object,
)

_SOCKET_PATH = Path("/etc/millefeuille/gpt-summary-publication.sock")
_SOCKET_OWNER_UID = 0
_METADATA_LIMIT = 131072
_REPLY_LIMIT = 4096
_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "execution_approval",
        "run_id",
        "source_pack_root",
        "evidence",
        "packet",
        "receipt",
    }
)
_APPROVAL_FIELDS = frozenset(
    {
        "manifest_sha256",
        "request_count",
        "packet_digest",
        "receipt_digest",
        "receipt_id",
        "paper_id",
        "provider_calls_performed",
    }
)
_SCHEMA = "millefeuille-gpt-summary-publication-request/v0.1"


@dataclass(frozen=True)
class GptSummaryPublicationAck:
    run_id: str
    receipt_digest: str
    bundle_manifest_sha256: str
    file_count: int


def request_gpt_summary_publication(
    *,
    outcome: TrustedGptSummaryOutcome,
    run_id: str,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
) -> GptSummaryPublicationAck:
    """Send one validated transient bundle to the fixed root socket."""

    evidence = {
        "route_evidence_path": str(route_evidence_path),
        "structure_evidence_path": str(structure_evidence_path),
        "preparation_path": str(preparation_path),
    }
    _require_evidence_paths(evidence, str(source_pack_root))
    bundle = plan_gpt_summary_publication_bundle(
        outcome=outcome,
        run_id=run_id,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
        **evidence,
    )
    handoff = encode_gpt_summary_outcome_handoff(outcome)
    metadata = _canonical_json(
        {
            "schema_version": _SCHEMA,
            "execution_approval": asdict(outcome.approval),
            "run_id": run_id,
            "source_pack_root": str(source_pack_root),
            "evidence": evidence,
            "packet": packet.to_dict(),
            "receipt": receipt.to_dict(),
        }
    ).encode("utf-8")
    if len(metadata) > _METADATA_LIMIT or len(handoff) > _HANDOFF_LIMIT:
        raise MillefeuilleContractError("GPT publication request is too large")
    _require_socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(300)
            channel.connect(str(_SOCKET_PATH))
            _require_peer_uid(channel, _SOCKET_OWNER_UID)
            channel.sendall(struct.pack("!I", len(metadata)) + metadata)
            channel.sendall(struct.pack("!I", len(handoff)))
            channel.sendall(handoff)
            size = struct.unpack("!I", _read_exact(channel, 4))[0]
            if size > _REPLY_LIMIT:
                raise MillefeuilleContractError("GPT publication reply is too large")
            encoded_reply = _read_exact(channel, size)
            reply = json.loads(
                encoded_reply.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise MillefeuilleContractError(
            "GPT publication socket result is uncertain; inspect root audit"
        ) from exc
    if (
        not isinstance(reply, dict)
        or set(reply)
        != {
            "status",
            "run_id",
            "receipt_digest",
            "bundle_manifest_sha256",
            "file_count",
        }
        or _canonical_json(reply).encode("utf-8") != encoded_reply
    ):
        raise MillefeuilleContractError("GPT publication reply is invalid")
    if reply["status"] != "published":
        raise MillefeuilleContractError(
            "GPT publication was not acknowledged; inspect root audit"
        )
    if (
        reply["run_id"] != run_id
        or not _digest_matches(reply["receipt_digest"], receipt.content_digest)
        or not _digest_matches(
            reply["bundle_manifest_sha256"], bundle.bundle_manifest_sha256
        )
        or type(reply["file_count"]) is not int
        or reply["file_count"] != len(bundle.files)
    ):
        raise MillefeuilleContractError("GPT publication reply identity drift")
    return GptSummaryPublicationAck(
        run_id=run_id,
        receipt_digest=receipt.content_digest,
        bundle_manifest_sha256=bundle.bundle_manifest_sha256,
        file_count=len(bundle.files),
    )


def serve_one_publication(*, timeout_seconds: int = 120) -> None:
    """Accept one node-user request, publish through root, and exit."""

    if (
        sys.platform != "linux"
        or os.geteuid() != 0
        or not hasattr(socket, "SO_PEERCRED")
    ):
        raise MillefeuilleContractError("GPT publication socket requires Linux root")
    _require_control_dir()
    if _SOCKET_PATH.parent != _CONTROL_DIR:
        raise MillefeuilleContractError("GPT publication socket path drifted")
    if _SOCKET_PATH.exists() or _SOCKET_PATH.is_symlink():
        raise MillefeuilleContractError("GPT publication socket already exists")
    import pwd

    model_user = pwd.getpwnam("node")
    bound_inode: tuple[int, int] | None = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            previous_umask = os.umask(0o777)
            try:
                listener.bind(str(_SOCKET_PATH))
                bound = _SOCKET_PATH.lstat()
                bound_inode = (bound.st_dev, bound.st_ino)
            finally:
                os.umask(previous_umask)
            listener.listen(1)
            listener.settimeout(timeout_seconds)
            os.chown(_SOCKET_PATH, 0, model_user.pw_gid)
            os.chmod(_SOCKET_PATH, 0o660)
            with listener.accept()[0] as channel:
                channel.settimeout(30)
                _require_peer_uid(channel, model_user.pw_uid)
                try:
                    metadata_size = struct.unpack("!I", _read_exact(channel, 4))[0]
                    if metadata_size > _METADATA_LIMIT:
                        raise MillefeuilleContractError(
                            "GPT publication metadata is too large"
                        )
                    encoded_metadata = _read_exact(channel, metadata_size)
                    handoff_size = struct.unpack("!I", _read_exact(channel, 4))[0]
                    if handoff_size > _HANDOFF_LIMIT:
                        raise MillefeuilleContractError(
                            "GPT publication handoff is too large"
                        )
                    handoff = _read_exact(channel, handoff_size)
                    request = _parse_request(encoded_metadata)
                except Exception:
                    reply = _reply("denied")
                else:
                    try:
                        result = publish_trusted_gpt_summary_handoff(
                            encoded_outcome=handoff, **request
                        )
                    except Exception:
                        # A receipt may already be consumed or a rename may
                        # already have happened. Only the root audit can say.
                        reply = _reply("uncertain")
                    else:
                        reply = _reply(
                            "published",
                            run_id=result.run_id,
                            receipt_digest=request["receipt"].content_digest,
                            bundle_manifest_sha256=result.bundle_manifest_sha256,
                            file_count=result.file_count,
                        )
                encoded_reply = _canonical_json(reply).encode("utf-8")
                channel.sendall(struct.pack("!I", len(encoded_reply)) + encoded_reply)
    finally:
        if bound_inode is not None:
            try:
                current = _SOCKET_PATH.lstat()
                if (current.st_dev, current.st_ino) == bound_inode:
                    _SOCKET_PATH.unlink()
            except FileNotFoundError:
                pass


def _parse_request(encoded: bytes) -> dict[str, Any]:
    payload = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    if (
        not isinstance(payload, dict)
        or set(payload) != _METADATA_FIELDS
        or payload["schema_version"] != _SCHEMA
        or _canonical_json(payload).encode("utf-8") != encoded
        or not isinstance(payload["run_id"], str)
        or not isinstance(payload["source_pack_root"], str)
        or not isinstance(payload["evidence"], dict)
        or set(payload["evidence"])
        != {"route_evidence_path", "structure_evidence_path", "preparation_path"}
        or any(not isinstance(value, str) for value in payload["evidence"].values())
    ):
        raise MillefeuilleContractError("GPT publication metadata is invalid")
    approval = payload["execution_approval"]
    if (
        not isinstance(approval, dict)
        or set(approval) != _APPROVAL_FIELDS
        or any(
            not isinstance(approval[key], str)
            for key in _APPROVAL_FIELDS
            if key not in {"request_count", "provider_calls_performed"}
        )
        or type(approval["request_count"]) is not int
        or approval["request_count"] < 1
        or type(approval["provider_calls_performed"]) is not int
        or approval["provider_calls_performed"] != 0
    ):
        raise MillefeuilleContractError("GPT publication execution approval is invalid")
    packet = OperatorPreflightPacket.from_dict(payload["packet"])
    receipt = ApprovedLiveReceipt.from_dict(payload["receipt"])
    _require_evidence_paths(payload["evidence"], payload["source_pack_root"])
    return {
        "execution_approval": SummaryApprovalPreview(**approval),
        "run_id": payload["run_id"],
        "source_pack_root": payload["source_pack_root"],
        "packet": packet,
        "receipt": receipt,
        **payload["evidence"],
    }


def _require_socket_path() -> None:
    try:
        parent = _SOCKET_PATH.parent.lstat()
        detail = _SOCKET_PATH.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            "GPT publication socket is unavailable"
        ) from exc
    if (
        os.name != "posix"
        or not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISSOCK(detail.st_mode)
        or parent.st_uid != _SOCKET_OWNER_UID
        or detail.st_uid != _SOCKET_OWNER_UID
        or stat.S_IMODE(parent.st_mode) & 0o022
        or stat.S_IMODE(detail.st_mode) & 0o007
    ):
        raise MillefeuilleContractError("GPT publication socket is not root-owned")


def _reply(
    status: str,
    *,
    run_id: str = "",
    receipt_digest: str = "",
    bundle_manifest_sha256: str = "",
    file_count: int = 0,
) -> dict[str, Any]:
    return {
        "status": status,
        "run_id": run_id,
        "receipt_digest": receipt_digest,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "file_count": file_count,
    }


def _digest_matches(value: Any, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)


if __name__ == "__main__":
    serve_one_publication()
