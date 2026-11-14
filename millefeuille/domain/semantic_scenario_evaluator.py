"""Evaluate sealed candidate bytes against separately frozen source review.

Reviewer assertions and hashes are not authentication or model receipts.
"""

import json
import re

from millefeuille.domain.evidence_answer import canonical, seal
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest

_CLAIM_KINDS = {
    "prose": "verbatim_prose",
    "procedure": "verbatim_procedure",
    "table": "literal_table_value",
}
_TABLE_KEYS = {"table_id", "row_label", "column_label", "unit_literal"}
_LOCATOR_KEYS = (
    "span_id",
    "attachment_key",
    "page",
    "section_id",
    "source_sha256",
    "byte_start",
    "byte_end",
    "text_sha256",
)


def _reviewed_claim_metadata(review):
    """Bind literal claim metadata to the sealed independent corpus."""
    corpus = review["corpus"]
    if (
        type(corpus) is not dict
        or corpus["schema_version"] != "millefeuille-evidence-corpus/v1"
        or corpus["manifest_sha256"] != seal(corpus, "manifest_sha256")
        or corpus["manifest_sha256"] != review["corpus_sha256"]
        or type(corpus["attachments"]) is not list
        or type(corpus["spans"]) is not list
        or type(review["source_spans"]) is not dict
    ):
        raise MillefeuilleContractError("independent corpus binding drift")
    attachments = {}
    for a in corpus["attachments"]:
        if (
            type(a) is not dict
            or type(a["key"]) is not str
            or not a["key"]
            or a["key"] in attachments
            or type(a["pages"]) is not list
            or not a["pages"]
            or any(type(p) is not int or p < 1 for p in a["pages"])
            or len(set(a["pages"])) != len(a["pages"])
        ):
            raise MillefeuilleContractError("independent attachment identity drift")
        for key in ("source_sha256", "extraction_sha256"):
            if type(a[key]) is not str or not re.fullmatch(
                "sha256:[0-9a-f]{64}", a[key]
            ):
                raise MillefeuilleContractError("independent attachment hash drift")
        attachments[a["key"]] = a
    metadata = {}
    for span in corpus["spans"]:
        sid = span["span_id"]
        if type(sid) is not str or not sid or sid in metadata:
            raise MillefeuilleContractError("independent corpus span identity drift")
        source = review["source_spans"][sid]
        attachment = attachments[span["attachment_key"]]
        if (
            any(canonical(span[k]) != canonical(source[k]) for k in _LOCATOR_KEYS)
            or type(span["page"]) is not int
            or span["page"] not in attachment["pages"]
            or type(span["byte_start"]) is not int
            or span["byte_start"] < 0
            or type(span["byte_end"]) is not int
            or span["byte_end"] <= span["byte_start"]
            or span["byte_end"] - span["byte_start"]
            != len(source["text_utf8"].encode())
            or digest(source["text_utf8"].encode()) != span["text_sha256"]
            or span["source_sha256"] != attachment["source_sha256"]
            or source["extraction_sha256"] != attachment["extraction_sha256"]
            or type(span["section_id"]) is not str
            or not span["section_id"]
            or type(span["source_ref"]) is not str
            or not span["source_ref"]
            or span["kind"] not in _CLAIM_KINDS
        ):
            raise MillefeuilleContractError("independent corpus/source span drift")
        qualifiers = span["table_coordinates"]
        if span["kind"] == "table":
            if (
                type(qualifiers) is not dict
                or set(qualifiers) != _TABLE_KEYS
                or any(type(v) is not str or not v for v in qualifiers.values())
            ):
                raise MillefeuilleContractError(
                    "independent table coordinates malformed"
                )
        elif qualifiers is not None:
            raise MillefeuilleContractError("independent non-table qualifiers")
        metadata[sid] = {
            "kind": _CLAIM_KINDS[span["kind"]],
            "excerpt_ids": [sid],
            "text_utf8": source["text_utf8"],
            "qualifiers_utf8": qualifiers if qualifiers is not None else {},
        }
    if set(metadata) != set(review["source_spans"]):
        raise MillefeuilleContractError("independent corpus span inventory drift")
    return metadata


def evaluate_frozen_scenarios(
    *, independent_gold, candidate_responses, candidate_response_sha256s, source_review
):
    """Fail closed on malformed evidence; literal proofs confer no authority."""
    try:
        return _evaluate_frozen_scenarios(
            independent_gold=independent_gold,
            candidate_responses=candidate_responses,
            candidate_response_sha256s=candidate_response_sha256s,
            source_review=source_review,
        )
    except MillefeuilleContractError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise MillefeuilleContractError("malformed frozen scenario evidence") from exc


def _evaluate_frozen_scenarios(
    *, independent_gold, candidate_responses, candidate_response_sha256s, source_review
):
    metadata = _reviewed_claim_metadata(source_review)
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
            e["span_id"] in source_spans
            and canonical(e) == canonical(source_spans[e["span_id"]])
            for e in excerpts
        )
        actual_locators = [
            (e["attachment_key"], e["page"], e["section_id"], e["text_sha256"])
            for e in excerpts
        ]
        expected_locators = [tuple(x) for x in gold["expected_locators"]]
        claims = response["qualified_claims"]
        excerpt_ids = [e["span_id"] for e in excerpts]
        claims_grounded = (
            type(claims) is list
            and len(set(excerpt_ids)) == len(excerpt_ids)
            and len(claims) == len(excerpts)
            and all(
                e["span_id"] in metadata
                and canonical(c) == canonical(metadata[e["span_id"]])
                for c, e in zip(claims, excerpts, strict=True)
            )
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
