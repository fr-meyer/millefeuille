"""Transient GPT card execution validation and metadata-only provenance plans.

These checks belong after an approved execution boundary. They neither
authorize a call nor publish or accept a complete paper analysis.
"""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.clients.openclaw_model_client import OpenClawModelExecution
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.model_executor import validate_model_executor_result
from millefeuille.domain.paper_card_inputs import plan_published_gpt_paper_card_request
from millefeuille.domain.paper_card_plan import (
    GptPaperCardRequestPlan,
    ValidatedPaperCardContent,
    validate_v1_paper_card_content,
)
from millefeuille.domain.summary_published_handoff import GptSummaryPublicationIdentity

CARD_PROVENANCE_SCHEMA_VERSION = "millefeuille-gpt-paper-card-provenance/v0.1"


@dataclass(frozen=True)
class ValidatedGptPaperCardExecution:
    paper_id: str
    run_id: str
    source_hash: str
    request_plan_sha256: str
    provenance_sha256: str
    content: ValidatedPaperCardContent = field(repr=False)
    executor_result: dict[str, Any] = field(repr=False)
    provenance_json: bytes = field(repr=False)
    incremental_cost: dict[str, Any] = field(repr=False)
    provider_calls_performed: int = 0
    writes_performed: int = 0


def validate_published_gpt_paper_card_execution(
    *,
    plan: GptPaperCardRequestPlan,
    execution: OpenClawModelExecution,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
) -> ValidatedGptPaperCardExecution:
    """Reverify source scope, actual execution, output bytes and observed usage.

    A valid result is transient evidence only. The caller must have used the
    trusted execution boundary; this function cannot authenticate or issue a
    receipt. Durable canonical card assembly and exact writes are later gates.
    """

    if not isinstance(plan, GptPaperCardRequestPlan) or not isinstance(
        execution, OpenClawModelExecution
    ):
        raise MillefeuilleContractError("GPT paper-card execution evidence is invalid")
    fresh = plan_published_gpt_paper_card_request(
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
        run_id=plan.run_id,
        timeout_seconds=plan.request["timeout_seconds"],
    )
    if plan != fresh:
        raise MillefeuilleContractError("GPT paper-card execution source or plan drift")
    result = validate_model_executor_result(
        request=fresh.request, result=execution.result
    )
    if result["status"] != "succeeded" or not isinstance(execution.output, bytes):
        raise MillefeuilleContractError("GPT paper-card execution did not succeed")
    if result["usage"] is None:
        raise MillefeuilleContractError("GPT paper-card observed usage is missing")
    if result["cost"] is not None and result["cost"] != {
        "currency": "USD",
        "micro_usd": 0,
    }:
        raise MillefeuilleContractError(
            "GPT paper-card reported a nonzero provider cost"
        )
    if result["output"] != {
        "sha256": _digest(execution.output),
        "bytes": len(execution.output),
    }:
        raise MillefeuilleContractError("GPT paper-card output binding drift")
    content = validate_v1_paper_card_content(execution.output, plan=fresh)
    # The exact request/result validator already requires subscription OAuth,
    # the pinned GPT route, and no fallback. This is the incremental API charge
    # basis, not an observed provider bill or the subscription's total price.
    # Preserve the raw result, including its unknown cost, without rewriting it.
    incremental_cost = {
        "currency": "USD",
        "micro_usd": 0,
        "basis": "subscription_oauth_no_incremental_api_charge",
    }
    provenance = {
        "schema_version": CARD_PROVENANCE_SCHEMA_VERSION,
        "paper_id": fresh.paper_id,
        "run_id": fresh.run_id,
        "source_hash": fresh.source_hash,
        "summary_publication_manifest_sha256": fresh.publication_manifest_sha256,
        "request_plan_sha256": fresh.manifest_sha256,
        "profile_id": "research-default",
        "task_kind": "paper_card",
        "request": fresh.request,
        "result": result,
        "incremental_cost": incremental_cost,
        "secret_material_persisted": False,
        "paper_text_persisted": False,
    }
    encoded = _canonical(provenance)
    return ValidatedGptPaperCardExecution(
        fresh.paper_id,
        fresh.run_id,
        fresh.source_hash,
        fresh.manifest_sha256,
        _digest(encoded),
        content,
        result,
        encoded,
        incremental_cost,
    )


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
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
