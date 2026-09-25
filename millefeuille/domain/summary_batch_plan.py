"""Provider-free, grounded GPT summary batch planning.

This is the supported handoff from a verified preparation package to a
versioned prompt batch and its text-free execution manifest. It makes no model
call and writes no paper artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from millefeuille.domain.summary_dispatch import (
    SUMMARY_OUTPUT_CONTRACTS,
    SummaryDispatchBatch,
    plan_verified_summary_dispatch,
)
from millefeuille.domain.summary_execution_manifest import (
    SummaryExecutionManifest,
    plan_summary_execution_manifest,
)
from millefeuille.domain.summary_prompts import build_v1_summary_prompt


@dataclass(frozen=True)
class GroundedSummaryBatchPlan:
    """Transient exact requests plus a private-text-free approval fingerprint."""

    manifest: SummaryExecutionManifest
    batch: SummaryDispatchBatch = field(repr=False)


def plan_grounded_gpt_summary_batch(
    *,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
) -> GroundedSummaryBatchPlan:
    """Plan every prepared unit with the bundled GPT-only v1 prompt contract."""

    evidence = {
        "route_evidence_path": route_evidence_path,
        "structure_evidence_path": structure_evidence_path,
        "preparation_path": preparation_path,
    }
    batch = plan_verified_summary_dispatch(
        **evidence,
        prompt_builder=build_v1_summary_prompt,
        output_contracts=SUMMARY_OUTPUT_CONTRACTS,
    )
    manifest = plan_summary_execution_manifest(batch=batch, **evidence)
    return GroundedSummaryBatchPlan(manifest=manifest, batch=batch)
