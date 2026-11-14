"""Source-qualified offline review -> decision -> six read-only output snapshots.

Receipts and hashes bind bytes; reviewer identity/scientific judgment remain
external assertions. No provider, publication or classification effects here.
"""

from contextlib import suppress
from copy import deepcopy
import json

from millefeuille.domain.acceptance_output_plan import json_bytes
from millefeuille.domain.evidence_answer import canonical
from millefeuille.domain.millefeuille import MillefeuilleContractError as Error
from millefeuille.domain.semantic_acceptance import (
    SCHEMA,
    budgeted_read,
    digest,
    safe_ref,
    scoped_reads,
    validate_semantic_decision,
)
from millefeuille.domain.semantic_output_plan import plan_semantic_acceptance
from millefeuille.domain.semantic_review_result import validate_review_result
from millefeuille.domain.semantic_scenario_evaluator import evaluate_frozen_scenarios
from millefeuille.domain.semantic_source_join import validate_source_review


@scoped_reads
def plan_reviewed_acceptance(
    *,
    review_result,
    resolved_materials,
    summary,
    required_evidence,
    candidate_responses,
    candidate_response_sha256s,
    independent_gold,
    source_review,
    independent_review_record,
):
    join, pages, sections = validate_source_review(
        resolved=resolved_materials, source_review=source_review
    )
    review = validate_review_result(review_result, resolved_materials)
    coverage = review["coverage"]
    if any(
        coverage.get(k) != expected
        for k, expected in [
            ("observed_pages", pages),
            ("covered_pages", pages),
            ("observed_sections", sections),
            ("covered_sections", sections),
        ]
    ):
        raise Error("scientific source coverage incomplete")
    spans = source_review["source_spans"]
    expected_bindings = [
        {
            "attachment_key": s["attachment_key"],
            "sha256": s["source_sha256"],
            "page": s["page"],
            "section": s["section_id"],
            "span_id": sid,
        }
        for sid, s in spans.items()
    ]
    if (
        review["source_bindings"] != expected_bindings
        or review["claims"] != source_review["reviewed_claims"]
    ):
        raise Error("reviewed claim/qualification or canonical locator drift")
    evaluation = evaluate_frozen_scenarios(
        independent_gold=independent_gold,
        candidate_responses=candidate_responses,
        candidate_response_sha256s=candidate_response_sha256s,
        source_review=source_review,
    )
    if (
        not all(s["passed"] for s in evaluation["scenarios"])
        or review["scenarios"] != evaluation["scenarios"]
    ):
        raise Error("all eight independently frozen scenarios must pass")
    unsealed = deepcopy(review_result)
    unsealed.pop("review_method", None)
    expected_receipt = {
        "source_review_sha256": digest(canonical(source_review)),
        "review_content_sha256": digest(canonical(unsealed)),
    }
    if independent_review_record != expected_receipt or review["review_method"][
        "independent_review_record_sha256"
    ] != digest(canonical(expected_receipt)):
        raise Error("independent review record drift")
    # The evidence that the decision validator will read must be those exact
    # source/evaluation/independent records, not unrelated role-shaped fixtures.
    records = []
    for entry in required_evidence:
        payload = budgeted_read(
            resolved_materials.run_dir / safe_ref(entry["ref"]),
            "review evidence",
            max_bytes=16777216,
        )
        if digest(payload) != entry["sha256"]:
            raise Error("stale review evidence")
        with suppress(UnicodeError, ValueError):
            records.append((entry["role"], json.loads(payload)))
    for role, record in [
        ("source", source_review),
        ("source", review_result),
        ("source", independent_review_record),
        ("retrieval", evaluation),
    ]:
        if not any(
            actual_role == role and actual == record for actual_role, actual in records
        ):
            raise Error("actual review/evaluation evidence binding missing")
    decision = {
        "schema_version": SCHEMA,
        "paper_id": join.paper_id,
        "run_id": join.run_id,
        "source_hash": join.source_hash,
        "reviewer": {
            "identity": review["review_method"]["reviewer_assertion"],
            "method": review["review_method"]["method"],
        },
        "outcome": "pass",
        **dict.fromkeys(
            ["coverage", "citations", "claims", "versions", "retrieval"], "pass"
        ),
        "limitations": review["review_method"]["limitations"],
        "evidence": required_evidence,
        "material_binding": review["material_binding"],
        "structural_sha256": digest(json_bytes(summary.to_dict())),
        "inventory_sha256": digest(
            json.dumps({"evidence": required_evidence}, sort_keys=True).encode()
        ),
        "canonical_join_sha256": digest(canonical(join.to_dict())),
        "review_result_sha256": review["review_result_sha256"],
        "scenario_result_sha256": evaluation["sha256"],
    }
    validate_semantic_decision(
        decision,
        root=resolved_materials.run_dir,
        paper_id=join.paper_id,
        run_id=join.run_id,
        source_hash=join.source_hash,
        required_evidence=required_evidence,
    )
    exact_plan = plan_semantic_acceptance(
        resolved=resolved_materials,
        summary=summary,
        decision=decision,
        required_evidence=required_evidence,
    )
    if len(exact_plan) != 6:
        raise Error("six distinct acceptance output roles required")
    return {
        "decision": decision,
        "evaluation": evaluation,
        "review": review,
        "canonical_join": join,
        "exact_plan": exact_plan,
    }
