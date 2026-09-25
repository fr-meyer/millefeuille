"""Read-only handoff from verified summary preparation to typed GPT requests.

The returned payloads exist only in memory. Planning does not call OpenClaw,
materialize summaries, or write to source packs, indexes, or Zotero.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_execution import SUMMARY_MODEL_STAGES
from millefeuille.domain.model_executor import build_summary_work_unit_request
from millefeuille.domain.secure_io import read_bytes_no_follow
from millefeuille.domain.summary_preparation import verify_summary_preparation_package

PromptBuilder = Callable[[str, str, tuple[str, ...], bytes, bytes], bytes]
GPT_MODEL = "openai/gpt-5.6-sol"


@dataclass(frozen=True)
class SummaryDispatchUnit:
    paper_id: str
    stage: str
    unit_id: str
    request: dict[str, Any]
    input_payload: bytes = field(repr=False)


@dataclass(frozen=True)
class SummaryDispatchBatch:
    paper_id: str
    preparation_sha256: str
    units: tuple[SummaryDispatchUnit, ...]


def plan_verified_summary_dispatch(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    prompt_builder: PromptBuilder,
    output_contracts: Mapping[str, tuple[str, str]],
    timeout_seconds: int = 180,
) -> SummaryDispatchBatch:
    """Build every typed GPT request from one reverified preparation package.

    The caller supplies a versioned prompt builder and exact output contracts.
    The caller must separately authorize execution and validate model outputs;
    this function cannot dispatch a provider call.
    """

    if not callable(prompt_builder):
        raise MillefeuilleContractError("summary prompt_builder must be callable")
    if not isinstance(output_contracts, Mapping):
        raise MillefeuilleContractError("summary output contracts must be a mapping")
    if set(output_contracts) != set(SUMMARY_MODEL_STAGES):
        raise MillefeuilleContractError(
            "summary output contracts must cover every summary stage exactly"
        )
    for stage in SUMMARY_MODEL_STAGES:
        contract = output_contracts[stage]
        if not isinstance(contract, tuple) or len(contract) != 2:
            raise MillefeuilleContractError(
                "summary output contract must be a schema id/version pair"
            )

    preparation = verify_summary_preparation_package(
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    if "structure page coverage incomplete" in preparation["execution"]["blockers"]:
        raise MillefeuilleContractError(
            "summary dispatch requires complete structure page coverage"
        )
    markdown = _read_bound_input(preparation["inputs"]["selected_markdown"])
    structure = _read_bound_input(preparation["inputs"]["structure"])
    prepared_bytes = read_bytes_no_follow(
        Path(preparation_path), "summary preparation package"
    )
    units: list[SummaryDispatchUnit] = []
    request_ids: set[str] = set()
    for stage in SUMMARY_MODEL_STAGES:
        schema_id, schema_version = output_contracts[stage]
        for work_unit in preparation["work_units"][stage]:
            unit_id = work_unit["unit_id"]
            locators = tuple(work_unit["source_locators"])
            payload = prompt_builder(stage, unit_id, locators, markdown, structure)
            if not isinstance(payload, bytes):
                raise MillefeuilleContractError(
                    "summary prompt_builder must return bytes"
                )
            try:
                prompt_text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MillefeuilleContractError(
                    "summary prompt must be valid UTF-8"
                ) from exc
            if not prompt_text.strip():
                raise MillefeuilleContractError("summary prompt must contain text")
            request = build_summary_work_unit_request(
                execution_plan=preparation["execution_plans"][stage],
                work_unit=work_unit,
                input_payload=payload,
                output_schema_id=schema_id,
                output_schema_version=schema_version,
                requested_model=GPT_MODEL,
                timeout_seconds=timeout_seconds,
                fallback_models=[],
            )
            request_id = request["idempotency_key"]
            if request_id in request_ids:
                raise MillefeuilleContractError(
                    "summary dispatch request identity collision"
                )
            request_ids.add(request_id)
            units.append(
                SummaryDispatchUnit(
                    paper_id=preparation["paper_id"],
                    stage=stage,
                    unit_id=unit_id,
                    request=request,
                    input_payload=payload,
                )
            )
    # A source changed during planning if the bound reads no longer match.
    _read_bound_input(preparation["inputs"]["selected_markdown"])
    _read_bound_input(preparation["inputs"]["structure"])
    if (
        read_bytes_no_follow(Path(preparation_path), "summary preparation package")
        != prepared_bytes
    ):
        raise MillefeuilleContractError(
            "summary preparation changed during dispatch planning"
        )
    return SummaryDispatchBatch(
        paper_id=preparation["paper_id"],
        preparation_sha256="sha256:" + hashlib.sha256(prepared_bytes).hexdigest(),
        units=tuple(units),
    )


def _read_bound_input(binding: dict[str, Any]) -> bytes:
    payload = read_bytes_no_follow(Path(binding["path"]), "summary dispatch source")
    if (
        len(payload) != binding["bytes"]
        or hashlib.sha256(payload).hexdigest() != binding["sha256"]
    ):
        raise MillefeuilleContractError("summary dispatch source drift")
    return payload
