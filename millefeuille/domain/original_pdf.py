"""Opt-in native original PDF observations, never scientific acceptance."""

import hashlib
import json
import re

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.native_decode_boundary import (
    ORIGINAL_PDF_LIMITS,
    inspect_bounded_pdf,
)


def bind_original_pages(result, *, expected_sha256):
    """Validate a complete decoder result and bind raw page citations.

    Pure projection, not an isolation attestation or reviewed-package admission.
    The original hash and extraction snapshot hash have distinct namespaces.
    """
    if (
        type(expected_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or type(result) is not dict
        or result.get("outcome") != "needs-review"
        or result.get("source_sha256") != expected_sha256
        or result.get("limits_enforced") is not True
        or result.get("child_reaped") is not True
        or type(result.get("exit_code")) is not int
        or result.get("exit_code") != 0
    ):
        raise MillefeuilleContractError("incomplete original PDF observation")
    pages = result.get("pages")
    if type(pages) is not list or not 1 <= len(pages) <= 32:
        raise MillefeuilleContractError("invalid original PDF pages")
    rows = []
    total = 0
    for number, page in enumerate(pages, 1):
        if (
            type(page) is not dict
            or set(page) != {"page", "text", "text_sha256"}
            or type(page["page"]) is not int
            or page["page"] != number
            or type(page["text"]) is not str
        ):
            raise MillefeuilleContractError("invalid original PDF page binding")
        if len(page["text"]) > 131072:
            raise MillefeuilleContractError("original PDF text too large")
        raw = page["text"].encode("utf-8")
        total += len(raw)
        if total > 131072 or hashlib.sha256(raw).hexdigest() != page["text_sha256"]:
            raise MillefeuilleContractError("original PDF text budget or hash drift")
        rows.append(dict(page, source_sha256=expected_sha256))
    snapshot = json.dumps(
        pages, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if len(snapshot) > 262096:
        raise MillefeuilleContractError("extraction snapshot too large")
    return {
        "schema": "millefeuille-original-pdf-observation/v1",
        "source_sha256": expected_sha256,
        "extraction_sha256": hashlib.sha256(snapshot).hexdigest(),
        "snapshot_encoding": "canonical-page-json-ascii/v1",
        "pages": rows,
        "disposition": "needs-review",
        "limitations": [
            "raw one-based PDF pages, not printed page labels",
            "no OCR, visual, figure, equation or semantic acceptance",
            "empty pages and competing statements remain unresolved",
            "not a reviewed evidence corpus or canonical write authority",
        ],
    }


def inspect_original_pdf(input_manifest):
    """Use the existing boundary, finite opt-in policy, no provider fallback."""
    result = inspect_bounded_pdf(input_manifest, ORIGINAL_PDF_LIMITS)
    if result.get("outcome") == "refused":
        return result
    try:
        observation = bind_original_pages(
            result, expected_sha256=input_manifest["sha256"]
        )
    except (MillefeuilleContractError, UnicodeError):
        return {"outcome": "refused", "reason": "invalid-original-page-binding"}
    return dict(result, original_pdf=observation)
