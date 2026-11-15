"""Pure exact classification snapshot; no taxonomy invention or write authority."""

from millefeuille.domain.acceptance_output_plan import plan_exact_outputs
from millefeuille.domain.classification import (
    _prepare_classification_from_evidence,
    render_classification_outputs,
)


def plan_classification_from_evidence(
    *,
    evidence_path,
    source_pack_root,
    run_id,
    paper_id=None,
    item_key=None,
    default_profile=None,
    artifact_root=None,
    stage_manifest=None,
):
    prepared = _prepare_classification_from_evidence(
        evidence_path=evidence_path,
        source_pack_root=source_pack_root,
        run_id=run_id,
        paper_id=paper_id,
        item_key=item_key,
        default_profile=default_profile,
        artifact_root=artifact_root,
        stage_manifest=stage_manifest,
    )
    return plan_exact_outputs(
        root=prepared.resolved.run_dir,
        outputs=render_classification_outputs(prepared),
    )
