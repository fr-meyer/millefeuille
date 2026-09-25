"""Strict, bounded transport for transient GPT summary execution evidence.

The receiver must supply an independently validated execution approval and
current source paths. Decoding replans the batch and accepts every result;
serialized Python objects are never trusted or unpickled.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.summary_batch_plan import plan_grounded_gpt_summary_batch
from millefeuille.domain.summary_dispatch import SummaryDispatchBatch
from millefeuille.domain.summary_execution_scope import SummaryApprovalPreview
from millefeuille.domain.summary_live_execution import TrustedGptSummaryOutcome
from millefeuille.domain.summary_results import accept_summary_execution_batch

_SCHEMA = "millefeuille-gpt-summary-outcome-handoff/v0.1"
_MAX_BYTES = 64 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 100_000
_ROOT_FIELDS = frozenset({"schema_version", "approval", "executions"})
_EXECUTION_FIELDS = frozenset({"result", "output_base64"})


def encode_gpt_summary_outcome_handoff(outcome: TrustedGptSummaryOutcome) -> bytes:
    """Encode successful transient evidence for a separate trusted receiver."""

    if (
        not isinstance(outcome, TrustedGptSummaryOutcome)
        or not isinstance(outcome.approval, SummaryApprovalPreview)
        or not isinstance(outcome.batch, SummaryDispatchBatch)
        or not isinstance(outcome.executions, tuple)
        or len(outcome.executions) != len(outcome.batch.units)
    ):
        raise MillefeuilleContractError("GPT summary handoff outcome is invalid")
    executions: list[dict[str, Any]] = []
    for execution in outcome.executions:
        if not isinstance(execution, OpenClawModelExecution) or not isinstance(
            execution.output, bytes
        ):
            raise MillefeuilleContractError("GPT summary handoff execution is invalid")
        executions.append(
            {
                "result": execution.result,
                "output_base64": base64.b64encode(execution.output).decode("ascii"),
            }
        )
    try:
        payload = {
            "schema_version": _SCHEMA,
            "approval": asdict(outcome.approval),
            "executions": executions,
        }
        _require_bounded_json_shape(payload)
        encoded = _canonical_json(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise MillefeuilleContractError("GPT summary handoff is not JSON") from exc
    if len(encoded) > _MAX_BYTES:
        raise MillefeuilleContractError("GPT summary handoff is too large")
    return encoded


def decode_gpt_summary_outcome_handoff(
    encoded: bytes,
    *,
    expected_approval: SummaryApprovalPreview,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> TrustedGptSummaryOutcome:
    """Rebuild an outcome only after current-source and result validation."""

    if (
        not isinstance(encoded, bytes)
        or len(encoded) > _MAX_BYTES
        or not isinstance(expected_approval, SummaryApprovalPreview)
    ):
        raise MillefeuilleContractError("GPT summary handoff input is invalid")
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _require_bounded_json_shape(payload)
        canonical = _canonical_json(payload)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise MillefeuilleContractError("GPT summary handoff JSON is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _ROOT_FIELDS
        or payload["schema_version"] != _SCHEMA
        or canonical != encoded
        or _canonical_json(payload["approval"])
        != _canonical_json(asdict(expected_approval))
        or not isinstance(payload["executions"], list)
    ):
        raise MillefeuilleContractError("GPT summary handoff identity drift")

    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    fresh = plan_grounded_gpt_summary_batch(**evidence)
    batch = fresh.batch
    if (
        fresh.manifest.sha256 != expected_approval.manifest_sha256
        or batch.paper_id != expected_approval.paper_id
        or len(batch.units) != expected_approval.request_count
        or len(payload["executions"]) != len(batch.units)
    ):
        raise MillefeuilleContractError("GPT summary handoff source or scope drift")
    executions: list[OpenClawModelExecution] = []
    for item in payload["executions"]:
        if (
            not isinstance(item, dict)
            or set(item) != _EXECUTION_FIELDS
            or not isinstance(item["result"], dict)
            or not isinstance(item["output_base64"], str)
        ):
            raise MillefeuilleContractError("GPT summary handoff execution is invalid")
        try:
            output = base64.b64decode(item["output_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MillefeuilleContractError(
                "GPT summary handoff output encoding is invalid"
            ) from exc
        if base64.b64encode(output).decode("ascii") != item["output_base64"]:
            raise MillefeuilleContractError(
                "GPT summary handoff output is not canonical"
            )
        executions.append(OpenClawModelExecution(item["result"], output))
    accepted = accept_summary_execution_batch(
        batch=batch,
        executions={
            (unit.stage, unit.unit_id): execution
            for unit, execution in zip(batch.units, executions, strict=True)
        },
        **evidence,
    )
    return TrustedGptSummaryOutcome(
        approval=expected_approval,
        accepted=accepted,
        batch=batch,
        executions=tuple(executions),
    )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate GPT summary handoff field")
        value[key] = item
    return value


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-standard GPT summary handoff JSON constant")


def _require_bounded_json_shape(value: Any) -> None:
    """Reject deep or cyclic structures before canonical JSON serialization."""

    pending = [(value, 0, False)]
    active: set[int] = set()
    nodes = 0
    while pending:
        item, depth, leaving = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise MillefeuilleContractError("GPT summary handoff JSON is too complex")
        if isinstance(item, (dict, list)):
            if isinstance(item, dict) and any(
                not isinstance(key, str) for key in item
            ):
                raise MillefeuilleContractError(
                    "GPT summary handoff JSON key is invalid"
                )
            identity = id(item)
            if identity in active:
                raise MillefeuilleContractError(
                    "GPT summary handoff JSON has cyclic containers"
                )
            active.add(identity)
            pending.append((item, depth, True))
            values = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1, False) for child in values)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise MillefeuilleContractError("GPT summary handoff JSON value is invalid")
