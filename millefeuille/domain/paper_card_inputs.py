"""Provider-free GPT card planning from a verified immutable summary run."""

from pathlib import Path

from millefeuille.domain.paper_card_plan import (
    GptPaperCardRequestPlan,
    plan_gpt_paper_card_request,
)
from millefeuille.domain.summary_published_handoff import (
    GptSummaryPublicationIdentity,
    load_published_gpt_summary_card_inputs,
)


def plan_published_gpt_paper_card_request(
    *,
    source_pack_root: str | Path,
    route_evidence_path: str | Path,
    structure_evidence_path: str | Path,
    preparation_path: str | Path,
    publication: GptSummaryPublicationIdentity,
    run_id: str,
    timeout_seconds: int = 180,
) -> GptPaperCardRequestPlan:
    """Verify all published input bytes and bind one no-call GPT card request.

    The expected publication identity must come from a trusted commit record.
    This reader does not create an approval or authorize any provider call.
    The new card run ID is explicit and participates in the prompt fingerprint.
    """

    inputs = load_published_gpt_summary_card_inputs(
        source_pack_root=source_pack_root,
        route_evidence_path=route_evidence_path,
        structure_evidence_path=structure_evidence_path,
        preparation_path=preparation_path,
        publication=publication,
    )
    return plan_gpt_paper_card_request(
        paper_id=inputs.handoff.publication.paper_id,
        run_id=run_id,
        source_hash=inputs.handoff.source_hash,
        publication_manifest_sha256=publication.bundle_manifest_sha256,
        markdown=inputs.markdown,
        structure=inputs.structure,
        summaries=inputs.summaries,
        timeout_seconds=timeout_seconds,
    )
