"""In-memory validation of complete GPT summary execution batches.

This module validates transient model output after the approved execution
boundary. It returns accepted text only in memory; no artifact is published.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import SUMMARY_MODEL_STAGES
from millefeuille.domain.model_executor import (
    validate_model_executor_request,
    validate_model_executor_result,
    verify_model_executor_input,
)
from millefeuille.domain.summary_dispatch import (
    GPT_MODEL,
    SUMMARY_OUTPUT_CONTRACTS,
    SummaryDispatchBatch,
    SummaryDispatchUnit,
)

_OUTPUT_FIELDS = frozenset(
    {"schema_version", "paper_id", "stage", "unit_id", "summary", "source_locators"}
)
_PAPER_ID = re.compile(r"[A-Za-z0-9._-]+\Z")


@dataclass(frozen=True)
class AcceptedSummaryUnit:
    paper_id: str
    stage: str
    unit_id: str
    source_locators: tuple[str, ...]
    executor_result: dict[str, Any]
    summary: str = field(repr=False)


@dataclass(frozen=True)
class AcceptedSummaryBatch:
    paper_id: str
    preparation_sha256: str
    units: tuple[AcceptedSummaryUnit, ...]


def validate_summary_unit_payload(unit: SummaryDispatchUnit, payload: Any) -> None:
    """Validate one parsed output; usable as the OpenClaw output validator."""

    request = _validated_unit_request(unit)
    if not isinstance(payload, dict) or set(payload) != _OUTPUT_FIELDS:
        raise MillefeuilleContractError("summary unit output fields are invalid")
    if payload["schema_version"] != request["output_contract"]["schema_version"]:
        raise MillefeuilleContractError("summary unit output schema drift")
    if payload["paper_id"] != unit.paper_id:
        raise MillefeuilleContractError("summary unit paper identity drift")
    if payload["stage"] != unit.stage or payload["unit_id"] != unit.unit_id:
        raise MillefeuilleContractError("summary unit task identity drift")
    summary = payload["summary"]
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 50000
        or "\x00" in summary
    ):
        raise MillefeuilleContractError("summary unit text is invalid")
    citations = payload["source_locators"]
    if (
        not isinstance(citations, list)
        or not citations
        or any(not isinstance(locator, str) for locator in citations)
        or len(citations) != len(set(citations))
        or citations
        != [locator for locator in unit.source_locators if locator in citations]
    ):
        raise MillefeuilleContractError("summary unit source locators are invalid")


def accept_summary_execution_batch(
    *,
    batch: SummaryDispatchBatch,
    executions: Mapping[tuple[str, str], OpenClawModelExecution],
) -> AcceptedSummaryBatch:
    """Accept every expected result atomically in memory or reject the batch."""

    if not isinstance(batch, SummaryDispatchBatch) or not isinstance(
        executions, Mapping
    ):
        raise MillefeuilleContractError("summary batch evidence is invalid")
    if not isinstance(batch.paper_id, str) or not _PAPER_ID.fullmatch(batch.paper_id):
        raise MillefeuilleContractError("summary batch paper identity is invalid")
    if not isinstance(batch.preparation_sha256, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", batch.preparation_sha256
    ):
        raise MillefeuilleContractError("summary batch preparation hash is invalid")
    if not batch.units or any(
        not isinstance(unit, SummaryDispatchUnit) for unit in batch.units
    ):
        raise MillefeuilleContractError("summary batch has invalid work units")
    keys = [(unit.stage, unit.unit_id) for unit in batch.units]
    if len(keys) != len(set(keys)) or set(executions) != set(keys):
        raise MillefeuilleContractError("summary batch execution coverage drift")
    accepted: list[AcceptedSummaryUnit] = []
    for unit in batch.units:
        if unit.paper_id != batch.paper_id:
            raise MillefeuilleContractError("summary batch paper identity drift")
        request = _validated_unit_request(unit)
        if _prompt_binding(unit) != batch.preparation_sha256:
            raise MillefeuilleContractError("summary batch preparation binding drift")
        execution = executions[(unit.stage, unit.unit_id)]
        if not isinstance(execution, OpenClawModelExecution):
            raise MillefeuilleContractError("summary batch execution is invalid")
        result = validate_model_executor_result(
            request=request, result=execution.result
        )
        if result["status"] != "succeeded" or not isinstance(execution.output, bytes):
            raise MillefeuilleContractError("summary batch execution did not succeed")
        if (
            result["output"]["bytes"] != len(execution.output)
            or result["output"]["sha256"]
            != "sha256:" + hashlib.sha256(execution.output).hexdigest()
        ):
            raise MillefeuilleContractError("summary batch output binding drift")
        try:
            payload = json.loads(
                execution.output.decode("utf-8"),
                object_pairs_hook=_strict_object_pairs,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise MillefeuilleContractError(
                "summary batch output is not strict JSON"
            ) from exc
        validate_summary_unit_payload(unit, payload)
        accepted.append(
            AcceptedSummaryUnit(
                paper_id=unit.paper_id,
                stage=unit.stage,
                unit_id=unit.unit_id,
                source_locators=tuple(payload["source_locators"]),
                executor_result=result,
                summary=payload["summary"],
            )
        )
    return AcceptedSummaryBatch(
        paper_id=batch.paper_id,
        preparation_sha256=batch.preparation_sha256,
        units=tuple(accepted),
    )


def _validated_unit_request(unit: SummaryDispatchUnit) -> dict[str, Any]:
    if not isinstance(unit, SummaryDispatchUnit):
        raise MillefeuilleContractError("summary dispatch unit is invalid")
    if unit.stage not in SUMMARY_MODEL_STAGES or not isinstance(
        unit.source_locators, tuple
    ):
        raise MillefeuilleContractError("summary dispatch unit stage is invalid")
    if not unit.source_locators or any(
        not isinstance(locator, str) or not locator.strip()
        for locator in unit.source_locators
    ):
        raise MillefeuilleContractError("summary dispatch unit locators are invalid")
    if len(unit.source_locators) != len(set(unit.source_locators)):
        raise MillefeuilleContractError("summary dispatch unit locators are duplicated")
    request = validate_model_executor_request(unit.request)
    verify_model_executor_input(request, unit.input_payload)
    _prompt_binding(unit)
    locators_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                list(unit.source_locators),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    if request["task"] != {
        "kind": unit.stage,
        "unit_id": unit.unit_id,
        "source_locator_count": len(unit.source_locators),
        "source_locators_sha256": locators_hash,
    }:
        raise MillefeuilleContractError("summary dispatch task binding drift")
    if (
        request["requested_model"] != GPT_MODEL
        or request["fallback"] != {"policy": "none", "models": []}
        or request["output_contract"]
        != {
            "schema_id": SUMMARY_OUTPUT_CONTRACTS[unit.stage][0],
            "schema_version": SUMMARY_OUTPUT_CONTRACTS[unit.stage][1],
        }
    ):
        raise MillefeuilleContractError(
            "summary dispatch model or output contract drift"
        )
    return request


def _prompt_binding(unit: SummaryDispatchUnit) -> str:
    prefix = b"Millefeuille summary source binding v0.1\n"
    if not unit.input_payload.startswith(prefix):
        raise MillefeuilleContractError("summary prompt source binding is missing")
    header_bytes, separator, prompt = unit.input_payload[len(prefix) :].partition(
        b"\n\n"
    )
    if not separator:
        raise MillefeuilleContractError("summary prompt source binding is malformed")
    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
        prompt_text = prompt.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MillefeuilleContractError(
            "summary prompt source binding is invalid"
        ) from exc
    if not prompt_text.strip() or not isinstance(header, dict):
        raise MillefeuilleContractError("summary prompt source binding is invalid")
    if set(header) != {
        "paper_id",
        "preparation_sha256",
        "stage",
        "unit_id",
        "source_locators",
    } or header != {
        "paper_id": unit.paper_id,
        "preparation_sha256": header["preparation_sha256"],
        "stage": unit.stage,
        "unit_id": unit.unit_id,
        "source_locators": list(unit.source_locators),
    }:
        raise MillefeuilleContractError("summary prompt source identity drift")
    digest = header["preparation_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise MillefeuilleContractError("summary prompt preparation hash is invalid")
    if (
        json.dumps(
            header,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        != header_bytes
    ):
        raise MillefeuilleContractError("summary prompt source binding is noncanonical")
    return digest


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-standard JSON constant")
