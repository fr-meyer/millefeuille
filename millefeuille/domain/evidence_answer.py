"""Literal, structured extractive answers. No inference, gold or model calls."""

from __future__ import annotations

import json
import re
from typing import TypedDict
import unicodedata

from millefeuille.domain.evidence_source_adapter import (
    BudgetExhaustedError,
    EvidenceCorpusManifestV1,
    ReadBudget,
    ScopedReadOnlyReader,
    iter_verified_spans,
)
from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.semantic_acceptance import digest


class ResponseLimitError(MillefeuilleContractError):
    """No serialized response can be returned within the supplied cap."""


class EvidenceQuestionV1(TypedDict):
    schema_version: str
    paper_id: str
    run_id: str
    corpus_sha256: str
    selection: dict
    intent: str
    terms: list[str]
    match: str
    evidence_kind: str
    table_selector: dict | None
    relation: str
    limits: dict


class EvidenceAnswerV1(TypedDict):
    outcome: str
    candidate_response_sha256: str


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def seal(value, field):
    return digest(canonical({k: v for k, v in value.items() if k != field}))


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError("unsupported structured grammar")


def _request(r):
    _keys(
        r,
        "schema_version paper_id run_id corpus_sha256 selection intent terms "
        "match evidence_kind table_selector relation limits",
    )
    if (
        r["schema_version"] != "millefeuille-evidence-question/v1"
        or r["intent"] not in {"explain", "procedure", "numeric", "compare", "page"}
        or r["match"] not in {"all_terms", "exact_phrase", "layout_all_terms"}
        or r["evidence_kind"] not in {"prose", "procedure", "table", "any"}
        or r["relation"] not in {"side_by_side_only", "none"}
    ):
        raise ValueError("unsupported structured grammar")
    _keys(r["selection"], "attachment_keys local_page section_ids")
    for key in ("attachment_keys", "section_ids"):
        v = r["selection"][key]
        if v is not None and (
            type(v) is not list
            or not v
            or len(v) > 64
            or any(type(x) is not str or not x for x in v)
            or len(set(v)) != len(v)
        ):
            raise ValueError("invalid selection")
    page = r["selection"]["local_page"]
    if page is not None and (type(page) is not int or not 1 <= page <= 100000):
        raise ValueError("invalid page")
    terms = r["terms"]
    if (
        type(terms) is not list
        or not (0 if r["intent"] == "page" else 1) <= len(terms) <= 8
        or any(
            type(t) is not str or not t.strip() or len(t.encode()) > 512 for t in terms
        )
    ):
        raise ValueError("invalid literal terms")
    if r["match"] == "exact_phrase" and len(terms) != 1:
        raise ValueError("one exact phrase required")
    _keys(r["limits"], "max_hits max_excerpt_bytes max_response_bytes")
    for k, maximum in [
        ("max_hits", 8),
        ("max_excerpt_bytes", 24576),
        ("max_response_bytes", 65536),
    ]:
        if type(r["limits"][k]) is not int or not 1 <= r["limits"][k] <= maximum:
            raise ValueError("invalid answer limit")
    if r["table_selector"] is not None:
        _keys(r["table_selector"], "table_id row_label column_label unit_literal")
        if any(type(v) is not str or not v for v in r["table_selector"].values()):
            raise ValueError("literal table coordinates required")
    if r["intent"] == "numeric" and r["table_selector"] is None:
        raise ValueError("numeric requests require explicit coordinates")
    if r["intent"] == "compare" and (
        r["relation"] != "side_by_side_only"
        or not r["selection"]["attachment_keys"]
        or len(r["selection"]["attachment_keys"]) < 2
    ):
        raise ValueError("explicit cross-source selectors required")


def _norm(s):
    return unicodedata.normalize("NFC", s).casefold()


def _layout_text(s):
    """Search-only projection; never replace source or citation bytes.

    Single physical line breaks are eligible, not paragraph/page boundaries.
    Wrapped lexical hyphens cannot be distinguished from layout hyphens.
    """
    s = _norm(s)

    # Use Unicode letter categories, including decomposed combining marks.
    def letter_before(index):
        while index >= 0 and unicodedata.category(s[index]).startswith("M"):
            index -= 1
        return index >= 0 and s[index].isalpha()

    pattern = r"[-\u00ad][ \t]*(?:\r\n|\n|\r)[ \t]*|\u00ad"

    def join(match):
        end = match.end()
        if letter_before(match.start() - 1) and end < len(s) and s[end].isalpha():
            return ""
        return match.group()

    return re.sub(pattern, join, s)


def answer_evidence(
    *,
    request: EvidenceQuestionV1,
    corpus: EvidenceCorpusManifestV1,
    budget: ReadBudget,
    reader: ScopedReadOnlyReader,
) -> EvidenceAnswerV1:
    result = {
        "schema_version": "millefeuille-evidence-answer/v1",
        "request_sha256": digest(canonical(request)),
        "corpus_sha256": corpus.get("manifest_sha256"),
        "outcome": "unsupported_request",
        "excerpts": [],
        "qualified_claims": [],
        "ambiguity": None,
        "explanation": {
            "method": "none",
            "text_utf8": "",
            "claim_ids": [],
            "limitations": [
                "Literal structured search only; no scientific entailment."
            ],
        },
        "search_proof": {
            "scope_sha256": corpus.get("completeness", {}).get(
                "authorized_scope_sha256"
            ),
            "expected_spans": 0,
            "visited_spans": 0,
            "expected_bytes": 0,
            "read_bytes": 0,
            "all_authorized_selected_spans_visited": False,
            "unread_refs": [],
            "budget_exhausted": False,
        },
    }

    def finish():
        result["candidate_response_sha256"] = seal(result, "candidate_response_sha256")
        limits = request.get("limits", {}) if isinstance(request, dict) else {}
        cap = limits.get("max_response_bytes") if isinstance(limits, dict) else None
        if type(cap) is not int or not 1 <= cap <= 65536:
            raise ResponseLimitError("valid response byte cap required")
        if len(canonical(result)) > cap:
            raise ResponseLimitError("sealed outcome exceeds response byte cap")
        return result

    try:
        _request(request)
    except (ValueError, TypeError, KeyError):
        return finish()
    layout = request["match"] == "layout_all_terms"
    if layout:
        result["explanation"]["limitations"].append(
            "layout_all_terms: search-only removal of letter-internal soft hyphens "
            "and hyphen plus single line break; lexical hyphens at line ends "
            "may also join. Matches are not exact source-byte matches; original "
            "excerpts and citations are unchanged. No semantic entailment."
        )
    try:
        if (
            corpus["schema_version"] != "millefeuille-evidence-corpus/v1"
            or seal(corpus, "manifest_sha256") != request["corpus_sha256"]
            or corpus["manifest_sha256"] != request["corpus_sha256"]
            or any(corpus[k] != request[k] for k in ("paper_id", "run_id"))
        ):
            raise MillefeuilleContractError("corpus binding drift")
        _keys(
            corpus,
            "schema_version paper_id run_id manifest_sha256 "
            "attachments spans completeness",
        )
        attachments = {a["key"]: a for a in corpus["attachments"]}
        if len(attachments) != len(corpus["attachments"]):
            raise MillefeuilleContractError("duplicate attachment identity")
        for span in corpus["spans"]:
            _keys(
                span,
                "span_id attachment_key page section_id kind source_ref "
                "source_sha256 byte_start byte_end text_sha256 table_coordinates",
            )
            if (
                span["attachment_key"] not in attachments
                or span["page"] not in attachments[span["attachment_key"]]["pages"]
                or span["kind"] not in {"prose", "procedure", "table"}
                or (span["kind"] == "table" and not span["table_coordinates"])
            ):
                raise MillefeuilleContractError("invalid source span declaration")
        spans = corpus["spans"]
        if (
            not spans
            or len(spans) > 4096
            or len({s["span_id"] for s in spans}) != len(spans)
        ):
            raise MillefeuilleContractError("invalid span inventory")
        selection = request["selection"]
        page = selection["local_page"]
        keys = sorted({s["attachment_key"] for s in spans if s["page"] == page})
        if page is not None and selection["attachment_keys"] is None and len(keys) > 1:
            result.update(
                outcome="ambiguous",
                ambiguity={
                    "page": page,
                    "attachment_keys": keys,
                    "required_selector": "attachment_keys",
                },
            )
            return finish()
        selected = [
            s
            for s in spans
            if (
                selection["attachment_keys"] is None
                or s["attachment_key"] in selection["attachment_keys"]
            )
            and (page is None or s["page"] == page)
            and (
                selection["section_ids"] is None
                or s["section_id"] in selection["section_ids"]
            )
        ]
        proof = result["search_proof"]
        proof.update(
            expected_spans=len(selected),
            expected_bytes=sum(s["byte_end"] - s["byte_start"] for s in selected),
            unread_refs=[s["span_id"] for s in selected],
        )
        hits = []
        initial = budget.read_bytes
        try:
            for span in iter_verified_spans(
                corpus=corpus, selection=selection, budget=budget, reader=reader
            ):
                proof["visited_spans"] += 1
                proof["unread_refs"].remove(span["span_id"])
                text = (_layout_text if layout else _norm)(span["text_utf8"])
                match = all(
                    re.search(r"(?<!\w)" + re.escape(_norm(t)) + r"(?!\w)", text)
                    for t in request["terms"]
                )
                kind = request["evidence_kind"]
                if kind != "any" and span["kind"] != kind:
                    match = False
                if request["intent"] == "procedure" and span["kind"] != "procedure":
                    match = False
                if (
                    request["table_selector"] is not None
                    and span["table_coordinates"] != request["table_selector"]
                ):
                    match = False
                if match:
                    hits.append(span)
        except BudgetExhaustedError:
            proof["budget_exhausted"] = True
        proof["read_bytes"] = budget.read_bytes - initial
        complete = (
            not proof["unread_refs"]
            and not proof["budget_exhausted"]
            and corpus["completeness"]["all_selected_attachments_inventory_complete"]
            is True
            and corpus["completeness"]["expected_spans"] == len(spans)
            and corpus["completeness"]["expected_source_bytes"]
            == sum(s["byte_end"] - s["byte_start"] for s in spans)
        )
        proof["all_authorized_selected_spans_visited"] = complete
        result["outcome"] = "incomplete"
        if not complete or not selected:
            return finish()
        if not hits:
            result["outcome"] = "no_evidence"
            return finish()
        if (
            len(hits) > request["limits"]["max_hits"]
            or sum(len(s["text_utf8"].encode()) for s in hits)
            > request["limits"]["max_excerpt_bytes"]
        ):
            return finish()
        if request["intent"] == "compare" and set(selection["attachment_keys"]) != {
            s["attachment_key"] for s in hits
        }:
            return finish()
        for span in hits:
            result["excerpts"].append(
                {
                    k: span[k]
                    for k in (
                        "span_id",
                        "attachment_key",
                        "page",
                        "section_id",
                        "source_sha256",
                        "extraction_sha256",
                        "byte_start",
                        "byte_end",
                        "text_sha256",
                        "text_utf8",
                    )
                }
            )
            result["qualified_claims"].append(
                {
                    "kind": {
                        "prose": "verbatim_prose",
                        "procedure": "verbatim_procedure",
                        "table": "literal_table_value",
                    }[span["kind"]],
                    "excerpt_ids": [span["span_id"]],
                    "text_utf8": span["text_utf8"],
                    "qualifiers_utf8": span["table_coordinates"] or {},
                }
            )
        result["explanation"].update(
            method="extractive_side_by_side"
            if request["intent"] == "compare"
            else "extractive_single",
            text_utf8="\n".join(
                s["attachment_key"] + " states: " + s["text_utf8"] for s in hits
            ),
            claim_ids=[s["span_id"] for s in hits],
        )
        result["outcome"] = "supported"
        if len(canonical(result)) + 110 > request["limits"]["max_response_bytes"]:
            result.update(outcome="incomplete", excerpts=[], qualified_claims=[])
            result["explanation"].update(method="none", text_utf8="", claim_ids=[])
    except ResponseLimitError:
        raise
    except (
        MillefeuilleContractError,
        OSError,
        UnicodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        result.update(outcome="integrity_refused", excerpts=[], qualified_claims=[])
    return finish()
