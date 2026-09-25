"""Read-only administrator approval check for one grounded GPT summary batch.

The fixed approval record is separate from the caller-supplied MF-100 packet
and receipt. Matching it does not reserve a receipt or permit a model call.
"""

from __future__ import annotations

from datetime import datetime
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Any

from millefeuille.domain.live_receipts import ApprovedLiveReceipt
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.operator_preflight import OperatorPreflightPacket
from millefeuille.domain.summary_execution_scope import (
    SummaryApprovalPreview,
    validate_gpt_summary_approval_preview,
)

_TRUSTED_APPROVAL_PATH = Path("/etc/millefeuille/gpt-summary-approval.json")
_TRUSTED_APPROVAL_OWNER_UID = 0
_TRUSTED_APPROVAL_SCHEMA = "millefeuille-gpt-summary-approval/v0.1"
_TRUSTED_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "receipt_digest",
        "packet_digest",
        "manifest_sha256",
        "paper_id",
        "request_count",
        "approver_id",
        "approved_at",
    }
)
_RECORD_LIMIT = 8192


def validate_trusted_gpt_summary_approval(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    artifact_root: str | Path,
    source_pack_root: str | Path,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    now: datetime | None = None,
) -> SummaryApprovalPreview:
    """Replan the paper and match its exact receipt to a trusted file.

    The caller still needs a privileged, durable one-use reservation and a
    separate execution boundary before any model call.
    """

    preview = validate_gpt_summary_approval_preview(
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        artifact_root=artifact_root,
        source_pack_root=source_pack_root,
        packet=packet,
        receipt=receipt,
        now=now,
    )
    encoded = _read_administrator_file(_TRUSTED_APPROVAL_PATH)
    try:
        record = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "GPT summary approval record is invalid"
        ) from exc
    if (
        not isinstance(record, dict)
        or set(record) != _TRUSTED_APPROVAL_FIELDS
        or encoded != (_canonical_json(record) + "\n").encode("utf-8")
        or record["schema_version"] != _TRUSTED_APPROVAL_SCHEMA
        or record["receipt_id"] != receipt.receipt_id
        or record["paper_id"] != preview.paper_id
        or type(record["request_count"]) is not int
        or record["request_count"] != preview.request_count
        or record["approver_id"] != receipt.approval.approver_id
        or record["approved_at"] != receipt.approval.approved_at
        or not _digest_matches(record["receipt_digest"], preview.receipt_digest)
        or not _digest_matches(record["packet_digest"], preview.packet_digest)
        or not _digest_matches(record["manifest_sha256"], preview.manifest_sha256)
    ):
        raise MillefeuilleContractError("GPT summary approval identity does not match")
    return preview


def _read_administrator_file(path: Path) -> bytes:
    """Read a fixed file through held descriptors and check ownership there."""

    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise MillefeuilleContractError("trusted approval requires no-follow support")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        parent_fd = os.open(path.parent, flags | os.O_DIRECTORY)
        try:
            parent = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != _TRUSTED_APPROVAL_OWNER_UID
                or stat.S_IMODE(parent.st_mode) & 0o022
            ):
                raise MillefeuilleContractError(
                    "GPT summary approval directory is not administrator-owned"
                )
            file_fd = os.open(path.name, flags, dir_fd=parent_fd)
            try:
                detail = os.fstat(file_fd)
                if (
                    not stat.S_ISREG(detail.st_mode)
                    or detail.st_uid != _TRUSTED_APPROVAL_OWNER_UID
                    or stat.S_IMODE(detail.st_mode) & 0o022
                ):
                    raise MillefeuilleContractError(
                        "GPT summary approval file is not administrator-owned"
                    )
                encoded = os.read(file_fd, _RECORD_LIMIT + 1)
            finally:
                os.close(file_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        raise MillefeuilleContractError(
            "GPT summary has no readable administrator approval"
        ) from exc
    if len(encoded) > _RECORD_LIMIT:
        raise MillefeuilleContractError("GPT summary approval record is too large")
    return encoded


def _digest_matches(value: Any, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)


def _unique_json_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate approval field")
        result[key] = value
    return result


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
