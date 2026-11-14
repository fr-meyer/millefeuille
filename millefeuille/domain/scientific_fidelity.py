"""Operator evidence contracts, never a scientific acceptance authority.

Locators are generic dictionary/list paths into the actual isolated receipt.
An exact structural match does not establish rendered or mathematical meaning.
"""

from dataclasses import dataclass
import json
import math
import re
from typing import Any

from millefeuille.domain.native_decode_boundary import inspect_bounded_pdf


@dataclass(frozen=True)
class FactExpectation:
    fact_id: str
    expected_fact: str
    oracle_provenance: str
    page: int
    region: tuple[float, float, float, float]
    # Each path is relative to scientific.pages[page - 1].
    checks: tuple[tuple[tuple[str | int, ...], Any], ...]
    source_pdf_sha256: str
    coordinate_system: str = "PDF user space, bottom-left; unnormalized"
    requires_semantics: bool = False


def observe_scientific_source(input_manifest, resource_limits=None):
    """Same production boundary, no trusted-fixture decoder fallback."""
    return inspect_bounded_pdf(input_manifest, resource_limits, scientific=True)


def compare_scientific_fact(receipt: dict, expected: FactExpectation) -> dict:
    """Compare independently authored structural checks, preserving provenance."""
    if (
        type(expected.page) is not int
        or not 1 <= expected.page <= 32
        or not expected.oracle_provenance
        or type(expected.source_pdf_sha256) is not str
        or not re.fullmatch("[0-9a-f]{64}", expected.source_pdf_sha256)
        or expected.coordinate_system != "PDF user space, bottom-left; unnormalized"
        or len(expected.region) != 4
        or any(
            type(v) not in (int, float) or abs(v) > 1e12 or not math.isfinite(v)
            for v in expected.region
        )
        or expected.region[0] > expected.region[2]
        or expected.region[1] > expected.region[3]
    ):
        raise ValueError("invalid-scientific-expectation")
    if receipt.get("source_sha256") not in (None, expected.source_pdf_sha256):
        raise ValueError("scientific-source-binding-mismatch")
    scientific = receipt.get("scientific")
    if (
        isinstance(scientific, dict)
        and scientific.get("coordinate_system") != expected.coordinate_system
    ):
        raise ValueError("scientific-coordinate-system-mismatch")
    comparisons = []
    available = (
        receipt.get("outcome") == "needs-review"
        and receipt.get("limits_enforced") is True
        and receipt.get("child_reaped") is True
        and receipt.get("source_sha256") == expected.source_pdf_sha256
        and isinstance(receipt.get("scientific"), dict)
        and receipt.get("scientific", {}).get("schema")
        == "millefeuille-pdf-operators/v1"
    )
    for path, value in expected.checks:
        actual = None
        found = False
        if available:
            try:
                actual = receipt["scientific"]["pages"][expected.page - 1]
                for component in path:
                    actual = actual[component]
                found = True
            except (KeyError, IndexError, TypeError):
                actual = None
        comparisons.append(
            {
                "locator": ["scientific", "pages", expected.page - 1, *path],
                "expected": value,
                "actual": actual,
                "result": "match"
                if found and actual == value
                else ("mismatch" if found else "insufficient-evidence"),
            }
        )
    results = {item["result"] for item in comparisons}
    status = "structural-match-needs-review"
    if not comparisons or "insufficient-evidence" in results or not available:
        status = "insufficient-evidence"
    elif "mismatch" in results:
        status = "mismatch"
    elif expected.requires_semantics:
        status = "unresolved-semantic-evidence"
    return {
        "schema": "millefeuille-scientific-comparison/v1",
        "fact_id": expected.fact_id,
        "expected_fact": expected.expected_fact,
        "oracle_provenance": expected.oracle_provenance,
        "source_pdf_sha256": receipt.get("source_sha256"),
        "expected_source_pdf_sha256": expected.source_pdf_sha256,
        "extractor_route": "kernel-isolated embedded/vector PDF operators",
        "extractor_version": receipt.get("tool"),
        "page": expected.page,
        "region": expected.region,
        "coordinate_system": expected.coordinate_system,
        "comparisons": comparisons,
        "status": status,
        "disposition": "needs-review",
        "acceptance_decision": None,
        "boundary_output_sha256": receipt.get("output_sha256"),
    }


def scientific_log_receipt(
    receipt: dict,
    comparisons: list[dict],
    *,
    context: dict,
    case_id: str,
    source_sha256: dict,
) -> dict:
    """Bounded payload for transport framing; not a standalone stdout line."""
    payload = {
        "schema": "millefeuille-s04-scientific-evidence/v1",
        "runtime": context,
        "source_sha256": source_sha256,
        "case_id": case_id,
        "observation": receipt,
        "fact_comparisons": comparisons,
    }
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ValueError("scientific-log-budget-exhausted")
    return payload
