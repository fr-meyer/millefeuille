"""One receipt-bound, synthetic GPT OAuth acceptance call.

This is deliberately separate from paper execution. The prompt contains no
paper text and the only durable data is sanitized approval/executor evidence.
The administrator publishes exact approval evidence to a root-owned store
after authenticated human approval. A receipt's self-hash alone is insufficient.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

from millefeuille.clients.openclaw_model_client import OpenClawModelClient
from millefeuille.domain.live_receipts import (
    ApprovedLiveReceipt,
    ReceiptReplayState,
    build_approved_live_audit_record,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import (
    build_model_executor_request,
    validate_model_executor_result,
)
from millefeuille.domain.operator_preflight import (
    OperatorPreflightPacket,
    compute_operator_root_target_id,
    evaluate_operator_preflight,
)
from millefeuille.domain.secure_io import read_bytes_no_follow

_PROMPT = b'Return only this JSON object, with no explanation: {"canary":"ok"}'
_MODEL = "openai/gpt-5.6-sol"
_SELECTOR = "gpt-oauth-canary"
_STOP_CONDITIONS = (
    "auth-failure",
    "model-mismatch",
    "provider-error",
    "schema-failure",
)
_ROLLBACK_ACTIONS = ("stop-and-review",)
_TRUSTED_APPROVAL_PATH = Path("/etc/millefeuille/gpt-oauth-canary-approval.json")
_TRUSTED_APPROVAL_OWNER_UID = 0
_TRUSTED_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "receipt_digest",
        "packet_digest",
        "approver_id",
        "approved_at",
    }
)
_TRUSTED_APPROVAL_SCHEMA = "millefeuille-gpt-canary-approval/v0.1"


def run_gpt_oauth_canary(
    *,
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    ledger_dir: str | Path,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    client: OpenClawModelClient | None = None,
) -> dict[str, Any]:
    """Reserve one exact approval before dispatch and return sanitized proof.

    A reservation is final even when auth lookup or execution fails. The
    function never retries and never returns the provider's raw response.
    """

    if not isinstance(packet, OperatorPreflightPacket):
        raise MillefeuilleContractError("canary requires an operator packet")
    if not isinstance(receipt, ApprovedLiveReceipt):
        raise MillefeuilleContractError("canary requires an approved-live receipt")
    packet = OperatorPreflightPacket.from_dict(packet.to_dict())
    receipt = ApprovedLiveReceipt.from_dict(receipt.to_dict())
    if receipt.approval.approver_id != "fr-meyer":
        raise MillefeuilleContractError("canary approver is not authorized")

    root = Path(ledger_dir)
    _require_canary_scope(packet, receipt, root)
    _verify_trusted_approval(packet, receipt)
    request = build_model_executor_request(
        task_kind="structure",
        unit_id=_SELECTOR,
        source_locators=["synthetic:gpt-oauth-canary"],
        requested_model=_MODEL,
        thinking="xhigh",
        prompt_template_id="gpt-oauth-canary",
        prompt_template_version="v0.1",
        input_payload=_PROMPT,
        output_schema_id="millefeuille-gpt-oauth-canary",
        output_schema_version="v0.1",
        timeout_seconds=90,
        max_attempts=1,
        retry_on=[],
        fallback_models=[],
    )

    _require_private_ledger_dir(root)
    database = root / "gpt-oauth-canary.sqlite3"
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
        preflight = evaluate_operator_preflight(
            packet,
            explicit_mode="approved-live",
            approval_receipt=receipt,
            environment=environment,
            now=now,
            replay_state=replay_state,
        )
        if any(row.state != "present" for row in preflight.credential_readiness):
            raise MillefeuilleContractError("canary OAuth readiness marker is missing")
        audit = build_approved_live_audit_record(
            receipt,
            packet.to_approved_live_request(),
            evaluated_at=now,
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

    execution = (client or OpenClawModelClient()).execute(
        request=request,
        input_payload=_PROMPT,
        output_validator=_validate_canary_output,
    )
    result = validate_model_executor_result(request=request, result=execution.result)
    if result["status"] == "succeeded":
        output = execution.output
        if not isinstance(output, bytes) or (
            len(output) != result["output"]["bytes"]
            or not hmac.compare_digest(
                "sha256:" + hashlib.sha256(output).hexdigest(),
                result["output"]["sha256"],
            )
        ):
            raise MillefeuilleContractError("canary output binding drifted")
        try:
            parsed_output = json.loads(
                output.decode("utf-8"), object_pairs_hook=_unique_json_object
            )
        except (UnicodeError, ValueError) as exc:
            raise MillefeuilleContractError("canary output is not strict JSON") from exc
        _validate_canary_output(parsed_output)
    elif execution.output is not None:
        raise MillefeuilleContractError("failed canary returned provider output")
    if result["cost"] is not None and result["cost"]["micro_usd"] != 0:
        raise MillefeuilleContractError("canary reported a nonzero provider cost")
    return {
        "receipt_id": receipt.receipt_id,
        "receipt_digest": receipt.content_digest,
        "packet_digest": packet.content_digest,
        "provider_calls_reserved": 1,
        "raw_output_persisted": False,
        "executor_result": result,
    }


def _validate_canary_output(value: Any) -> None:
    if value != {"canary": "ok"}:
        raise MillefeuilleContractError("GPT canary output did not match its schema")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-standard JSON constant")


def _verify_trusted_approval(
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
) -> None:
    """Read a fixed administrator-owned approval record, never caller input."""

    path = _TRUSTED_APPROVAL_PATH
    try:
        parent = path.parent.lstat()
        detail = path.lstat()
    except FileNotFoundError as exc:
        raise MillefeuilleContractError(
            "GPT canary has no trusted administrator approval"
        ) from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISREG(detail.st_mode)
        or parent.st_uid != _TRUSTED_APPROVAL_OWNER_UID
        or detail.st_uid != _TRUSTED_APPROVAL_OWNER_UID
        or stat.S_IMODE(parent.st_mode) & 0o022
        or stat.S_IMODE(detail.st_mode) & 0o022
    ):
        raise MillefeuilleContractError(
            "GPT canary approval store is not administrator-owned"
        )
    try:
        encoded = read_bytes_no_follow(path, "GPT canary approval", max_bytes=8192)
        record = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise MillefeuilleContractError("GPT canary approval is invalid") from exc
    if (
        not isinstance(record, dict)
        or set(record) != _TRUSTED_APPROVAL_FIELDS
        or encoded != (_canonical_json(record) + "\n").encode("utf-8")
        or record["schema_version"] != _TRUSTED_APPROVAL_SCHEMA
        or record["receipt_id"] != receipt.receipt_id
        or record["approver_id"] != receipt.approval.approver_id
        or record["approved_at"] != receipt.approval.approved_at
        or not isinstance(record["receipt_digest"], str)
        or not isinstance(record["packet_digest"], str)
        or not hmac.compare_digest(
            record["receipt_digest"], receipt.content_digest
        )
        or not hmac.compare_digest(record["packet_digest"], packet.content_digest)
    ):
        raise MillefeuilleContractError("GPT canary approval identity does not match")


def _require_canary_scope(
    packet: OperatorPreflightPacket,
    receipt: ApprovedLiveReceipt,
    root: Path,
) -> None:
    source = packet.source
    provider = packet.provider
    expected_destination = (
        "artifact-root",
        compute_operator_root_target_id(packet.artifact_root),
    )
    if (
        packet.mode != "approved-live"
        or packet.operations != ("model.execute",)
        or source.adapter != "local-fixture"
        or source.selector.kind != "paper-id"
        or source.selector.value != _SELECTOR
        or source.item_cap != 1
        or source.resolved_item_count != 1
        or provider is None
        or (provider.provider_id, provider.model_id, provider.profile_id)
        != ("openai", "gpt-5.6-sol", "openclaw-subscription-oauth")
        or packet.max_provider_calls != 1
        or packet.max_cost_usd_micros != 0
        or packet.disposal_policy.pdfs != "not-applicable"
        or packet.disposal_policy.provider_payloads != "never-persist"
        or packet.disposal_policy.temporary_files != "delete-after-run"
        or packet.stop_conditions != _STOP_CONDITIONS
        or packet.rollback_actions != _ROLLBACK_ACTIONS
        or packet.acceptance_status != "not-applicable"
        or len(packet.destinations) != 1
        or (packet.destinations[0].kind, packet.destinations[0].id)
        != expected_destination
        or len(packet.targets) != 2
        or len(packet.credential_requirements) != 1
        or packet.credential_requirements[0].credential_type != "model-oauth"
        or packet.credential_requirements[0].reference
        != "OPENCLAW_CODEX_OAUTH_READY"
        or packet.artifact_root != str(root)
        or packet.source_pack_root != str(root)
        or packet.approval_receipt is None
        or packet.approval_receipt.receipt_id != receipt.receipt_id
        or packet.approval_receipt.content_digest != receipt.content_digest
    ):
        raise MillefeuilleContractError("packet is outside the GPT-only canary scope")


def _require_private_ledger_dir(root: Path) -> None:
    if os.name != "posix" or not root.is_absolute():
        raise MillefeuilleContractError("canary ledger requires an absolute POSIX path")
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise MillefeuilleContractError("canary ledger path has a symlink")
    with suppress(FileExistsError):
        root.mkdir(mode=0o700)
    detail = root.lstat()
    if (
        not stat.S_ISDIR(detail.st_mode)
        or detail.st_uid != os.getuid()
        or stat.S_IMODE(detail.st_mode) != 0o700
    ):
        raise MillefeuilleContractError("canary ledger directory is not private")


def _require_private_database_or_absent(path: Path) -> None:
    try:
        detail = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(detail.st_mode)
        or detail.st_uid != os.getuid()
        or stat.S_IMODE(detail.st_mode) != 0o600
    ):
        raise MillefeuilleContractError("canary audit database is not private")


def _parse_canonical_audit(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise MillefeuilleContractError("canary audit ledger is invalid") from exc
    if not isinstance(payload, dict) or _canonical_json(payload) != value:
        raise MillefeuilleContractError("canary audit ledger is not canonical")
    return payload


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
