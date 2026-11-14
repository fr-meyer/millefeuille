"""Opt-in offline native extraction seam, not an OCR or layout acceptance claim.

PyPDF2 is an optional local capability declared by the native extra.
No provider, output persistence, password recovery or automatic routing occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import re

from millefeuille.domain.millefeuille import MillefeuilleContractError
from millefeuille.domain.secure_io import read_bytes_no_follow


class SourceQualificationError(MillefeuilleContractError):
    """Safe typed refusal with a stable machine-readable reason."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class NativeSourceObservation:
    source_sha256: str
    pages: tuple[str, ...]
    text_sha256: str
    tool: str
    disposition: str = "needs-review"
    limitations: tuple[str, ...] = (
        "reading order follows content streams, not visual layout",
        "no table, figure, glyph, equation or semantic fidelity guarantee",
        "empty pages require separately approved OCR",
    )


def qualify_native_source(
    path: Path, *, expected_sha256: str
) -> NativeSourceObservation:
    """Trusted synthetic fixture seam only; never use for private originals.

    Decode exact verified bytes; return observations requiring source review.

    Byte and page caps bound inputs, not decoder CPU/memory for hostile PDFs.
    Use a separate resource-limited process for untrusted production documents.
    """
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise SourceQualificationError("invalid-expected-hash")
    try:
        raw = read_bytes_no_follow(path, "native qualification PDF", max_bytes=2**20)
    except MillefeuilleContractError as exc:
        raise SourceQualificationError("source-unreadable-or-unsafe") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise SourceQualificationError("source-hash-drift")
    if not raw.startswith(b"%PDF-"):
        raise SourceQualificationError("unsupported-format")
    try:
        import PyPDF2
    except ImportError as exc:
        raise SourceQualificationError("native-decoder-unavailable") from exc
    try:
        reader = PyPDF2.PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise SourceQualificationError("password-required")
        if not 1 <= len(reader.pages) <= 32:
            raise SourceQualificationError("page-count-out-of-bounds")
        pages = tuple(page.extract_text() or "" for page in reader.pages)
        if sum(len(page.encode("utf-8")) for page in pages) > 2**20:
            raise SourceQualificationError("extracted-text-too-large")
    except SourceQualificationError:
        raise
    except Exception as exc:
        raise SourceQualificationError("pdf-decode-error") from exc
    text_hash = hashlib.sha256(
        b"".join(len(p.encode()).to_bytes(8, "big") + p.encode() for p in pages)
    ).hexdigest()
    return NativeSourceObservation(
        digest, pages, text_hash, f"PyPDF2/{PyPDF2.__version__}"
    )


def compare_native_sources(
    left: NativeSourceObservation, right: NativeSourceObservation
) -> str:
    """Identity observations only; never choose an authoritative version."""
    if left.source_sha256 == right.source_sha256:
        return "exact-duplicate-needs-review"
    if left.text_sha256 == right.text_sha256:
        return "same-text-different-bytes-needs-review"
    return "competing-content-needs-review"


def qualify_untrusted_native_source(input_manifest, resource_limits=None):
    """Production/untrusted entrypoint, never delegates to the trusted fixture seam."""
    from millefeuille.domain.native_decode_boundary import inspect_bounded_pdf

    return inspect_bounded_pdf(input_manifest, resource_limits)
