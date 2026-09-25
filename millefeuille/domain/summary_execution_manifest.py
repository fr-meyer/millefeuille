"""Content-addressed, no-call plan for one complete GPT summary batch.

The manifest contains request identities and hashes, never prompts, source
text, provider output, or credentials. It is approval input, not authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from millefeuille.domain.model_executor import model_executor_request_sha256
from millefeuille.domain.summary_dispatch import GPT_MODEL, SummaryDispatchBatch
from millefeuille.domain.summary_results import verify_summary_dispatch_batch

SUMMARY_EXECUTION_MANIFEST_SCHEMA_VERSION = (
    "millefeuille-summary-execution-manifest/v0.1"
)


@dataclass(frozen=True)
class SummaryExecutionManifest:
    sha256: str
    json_bytes: bytes = field(repr=False)


def plan_summary_execution_manifest(
    *,
    batch: SummaryDispatchBatch,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> SummaryExecutionManifest:
    """Reverify all work units and fingerprint their exact no-call requests."""

    requests = verify_summary_dispatch_batch(
        batch=batch,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
    )
    units: list[dict[str, Any]] = []
    for unit, request in zip(batch.units, requests, strict=True):
        units.append(
            {
                "stage": unit.stage,
                "unit_id": unit.unit_id,
                "source_locator_count": request["task"]["source_locator_count"],
                "source_locators_sha256": request["task"]["source_locators_sha256"],
                "input": request["input"],
                "prompt_template": request["prompt_template"],
                "output_contract": request["output_contract"],
                "timeout_seconds": request["timeout_seconds"],
                "idempotency_key": request["idempotency_key"],
                "request_sha256": model_executor_request_sha256(request),
            }
        )
    document = {
        "schema_version": SUMMARY_EXECUTION_MANIFEST_SCHEMA_VERSION,
        "status": "planned-no-call",
        "paper_id": batch.paper_id,
        "preparation_sha256": batch.preparation_sha256,
        "requested_model": GPT_MODEL,
        "required_auth_class": "subscription_oauth",
        "fallback_policy": "none",
        "work_unit_count": len(units),
        "provider_calls_performed": 0,
        "units": units,
    }
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return SummaryExecutionManifest(
        sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        json_bytes=encoded,
    )
