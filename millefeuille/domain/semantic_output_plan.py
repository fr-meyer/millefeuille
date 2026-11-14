"""Render the six acceptance outputs for separately approved publication."""

from __future__ import annotations

from millefeuille.domain.acceptance import (
    ACCEPTANCE_SUMMARY_MARKDOWN_REF,
    ACCEPTANCE_SUMMARY_REF,
    _render_acceptance_markdown,
)
from millefeuille.domain.acceptance_output_plan import (
    json_bytes,
    plan_exact_outputs,
    validate_output_refs,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError, StageName
from millefeuille.domain.semantic_acceptance import (
    SEMANTIC_DECISION_REF,
    scoped_reads,
    validate_semantic_decision,
)
from millefeuille.domain.stage_runtime import (
    relative_ref,
    update_artifact_index,
    upsert_artifact_record,
    upsert_stage_record,
)


@scoped_reads
def plan_semantic_acceptance(*, resolved, summary, decision, required_evidence):
    """No writes. Caller owns source inventory and reviewer authentication.

    This plan cannot grant authority, freshness, publication, rollback or a
    complete classification. The parent must obtain the exact effects approval.
    """
    if summary.status.value != "pass":
        raise MillefeuilleContractError("structural acceptance must pass first")
    for field in ("paper_id", "run_id", "source_hash"):
        if getattr(summary, field) != getattr(resolved, field):
            raise MillefeuilleContractError("acceptance planning identity drift")
    import json

    from millefeuille.domain.semantic_acceptance import digest

    inventory_bytes = json.dumps(
        {"evidence": required_evidence}, sort_keys=True
    ).encode()
    if decision.get("structural_sha256") != digest(json_bytes(summary.to_dict())):
        raise MillefeuilleContractError("semantic structural acceptance drift")
    if decision.get("inventory_sha256") != digest(inventory_bytes):
        raise MillefeuilleContractError("semantic inventory binding drift")
    from millefeuille.domain.semantic_materials import require_material_binding

    require_material_binding(resolved, decision)
    validate_semantic_decision(
        decision,
        root=resolved.run_dir,
        paper_id=resolved.paper_id,
        run_id=resolved.run_id,
        source_hash=resolved.source_hash,
        required_evidence=required_evidence,
    )
    validate_output_refs(
        [
            ACCEPTANCE_SUMMARY_REF.as_posix(),
            ACCEPTANCE_SUMMARY_MARKDOWN_REF.as_posix(),
            SEMANTIC_DECISION_REF.as_posix(),
            "reports/semantic-review-inventory.json",
            relative_ref(resolved.stage_manifest_path, resolved.run_dir),
            relative_ref(resolved.artifact_index_path, resolved.run_dir),
        ]
    )
    outputs = {
        ACCEPTANCE_SUMMARY_REF.as_posix(): json_bytes(summary.to_dict()),
        ACCEPTANCE_SUMMARY_MARKDOWN_REF.as_posix(): _render_acceptance_markdown(
            summary
        ).encode("utf-8"),
        SEMANTIC_DECISION_REF.as_posix(): json_bytes(decision),
        "reports/semantic-review-inventory.json": json_bytes(
            {"evidence": required_evidence}
        ),
    }
    manifest = upsert_stage_record(
        resolved.stage_manifest,
        name=StageName.ACCEPTANCE,
        status="passed",
        outputs=list(outputs),
        notes=["snapshot source-reviewed acceptance; no remote freshness claim"],
    )
    protected = {
        ACCEPTANCE_SUMMARY_REF.as_posix(),
        ACCEPTANCE_SUMMARY_MARKDOWN_REF.as_posix(),
        SEMANTIC_DECISION_REF.as_posix(),
        "reports/semantic-review-inventory.json",
    }
    if any(
        f["root"] == "run" and f["ref"] in protected
        for f in decision["material_binding"]["files"]
    ):
        raise MillefeuilleContractError("semantic output aliases a material input")
    index = resolved.artifact_index
    for name, kind, ref, fmt in (
        ("acceptance_summary", "acceptance-summary", ACCEPTANCE_SUMMARY_REF, "json"),
        (
            "acceptance_summary_markdown",
            "acceptance-summary-markdown",
            ACCEPTANCE_SUMMARY_MARKDOWN_REF,
            "markdown",
        ),
        ("semantic_decision", "semantic-decision", SEMANTIC_DECISION_REF, "json"),
    ):
        index = upsert_artifact_record(
            index,
            name=name,
            kind=kind,
            ref=ref.as_posix(),
            format=fmt,
            stage="acceptance",
            private_content=True,
        )
    index = update_artifact_index(
        index, stage_name=StageName.ACCEPTANCE, stage_status="passed"
    )
    outputs[relative_ref(resolved.stage_manifest_path, resolved.run_dir)] = json_bytes(
        manifest.to_dict()
    )
    outputs[relative_ref(resolved.artifact_index_path, resolved.run_dir)] = json_bytes(
        index.to_dict()
    )
    from millefeuille.domain.secure_io import RootArtifactReader

    with RootArtifactReader(resolved.run_dir) as reader:
        return plan_exact_outputs(
            root=resolved.run_dir, outputs=outputs, artifact_reader=reader
        )
