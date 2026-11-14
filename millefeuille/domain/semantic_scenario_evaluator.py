"""Evaluate sealed candidate bytes against separately frozen source review.

Reviewer assertions and hashes are not authentication or model receipts.
"""

import json

from millefeuille.domain.evidence_answer import canonical, seal
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest


def evaluate_frozen_scenarios(
    *, independent_gold, candidate_responses, candidate_response_sha256s, source_review
):
    if (
        len(independent_gold) != 8
        or len(candidate_responses) != 8
        or len(candidate_response_sha256s) != 8
        or len({g["id"] for g in independent_gold}) != 8
    ):
        raise MillefeuilleContractError("exactly eight frozen scenarios required")
    if any(type(g) is not dict for g in independent_gold):
        raise MillefeuilleContractError("typed frozen scenarios required")
    for span_id, span in source_review["source_spans"].items():
        if (
            span["span_id"] != span_id
            or digest(span["text_utf8"].encode()) != span["text_sha256"]
            or type(span["page"]) is not int
            or span["page"] < 1
            or type(span["byte_start"]) is not int
            or type(span["byte_end"]) is not int
            or span["byte_end"] - span["byte_start"] != len(span["text_utf8"].encode())
        ):
            raise MillefeuilleContractError("independent source span drift")
    if source_review.get("frozen_gold_sha256") != digest(canonical(independent_gold)):
        raise MillefeuilleContractError("frozen independent gold drift")
    results = []
    for gold, raw, expected_hash in zip(
        independent_gold, candidate_responses, candidate_response_sha256s, strict=True
    ):
        if type(raw) is not bytes:
            raise MillefeuilleContractError("sealed candidate bytes required")
        response = json.loads(raw)
        if (
            canonical(response) != raw
            or digest(raw) != expected_hash
            or response.get("candidate_response_sha256")
            != seal(response, "candidate_response_sha256")
        ):
            raise MillefeuilleContractError("candidate response seal drift")
        excerpts = response["excerpts"]
        source_spans = source_review["source_spans"]
        grounded = all(
            e["span_id"] in source_spans and e == source_spans[e["span_id"]]
            for e in excerpts
        )
        actual_locators = [
            (e["attachment_key"], e["page"], e["section_id"], e["text_sha256"])
            for e in excerpts
        ]
        expected_locators = [tuple(x) for x in gold["expected_locators"]]
        claims_grounded = all(
            c["text_utf8"]
            in [e["text_utf8"] for e in excerpts if e["span_id"] in c["excerpt_ids"]]
            for c in response["qualified_claims"]
        )
        passed = (
            response["request_sha256"] == gold["query_sha256"]
            and response["corpus_sha256"] == source_review["corpus_sha256"]
            and response["outcome"] == gold["expected_verdict"]
            and actual_locators == expected_locators
            and grounded
            and claims_grounded
        )
        if response["outcome"] == "supported":
            expected_text = "\n".join(
                e["attachment_key"] + " states: " + e["text_utf8"] for e in excerpts
            )
            passed = (
                passed
                and bool(excerpts)
                and bool(response["qualified_claims"])
                and response["explanation"]["text_utf8"] == expected_text
                and response["explanation"]["claim_ids"]
                == [e["span_id"] for e in excerpts]
            )
        if response["outcome"] == "no_evidence":
            p = response["search_proof"]
            passed = (
                passed
                and gold.get("independent_scientific_absence_reviewed") is True
                and p["all_authorized_selected_spans_visited"] is True
                and p["visited_spans"] == p["expected_spans"] > 0
                and not p["unread_refs"]
                and not p["budget_exhausted"]
            )
        if response["outcome"] == "ambiguous":
            passed = (
                passed
                and response["ambiguity"] == gold.get("ambiguity")
                and not excerpts
            )
        results.append(
            {
                "id": gold["id"],
                "query_sha256": gold["query_sha256"],
                "expected_locators": gold["expected_locators"],
                "expected_verdict": gold["expected_verdict"],
                "observed_response_sha256": expected_hash,
                "observed_verdict": response["outcome"],
                "passed": bool(passed),
            }
        )
    record = {
        "schema_version": "millefeuille-semantic-scenario-results/v1",
        "frozen_gold_sha256": digest(canonical(independent_gold)),
        "source_review_sha256": digest(canonical(source_review)),
        "scenarios": results,
    }
    record["sha256"] = seal(record, "sha256")
    return record
