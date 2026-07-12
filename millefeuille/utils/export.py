"""Helpers for building and exporting discovered attachment URL records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import parse_qsl, urlparse

from millefeuille.clients.exceptions import (
    AttachmentIdentityError,
    HandoffSecurityError,
)
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.config import OpenKBHandoffExportConfig
from millefeuille.domain.models import (
    AttachmentInfo,
    DiscoveredAttachmentExportRecord,
    DiscoveredItem,
    OpenKBHandoffRow,
)

_logger = logging.getLogger(__name__)

_ZOTERO_RENAME_FORMULA = (
    '{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}'
    '{{ title truncate="125" }}'
)
_ZOTERO_RENAME_HINT = (
    f"Configure the Zotero rename formula {_ZOTERO_RENAME_FORMULA} "
    "to ensure canonical filenames."
)

_GENERIC_FILENAMES: frozenset[str] = frozenset({
    "file",
    "file.pdf",
    "document",
    "document.pdf",
    "unknown",
    "unknown.pdf",
})

_VALID_VERIFICATION_STRENGTHS: frozenset[str] = frozenset({
    "full",
    "hash-only",
    "metadata-only",
    "key-only",
})

_WEAK_VERIFICATION_STRENGTHS: frozenset[str] = frozenset({
    "hash-only",
    "metadata-only",
    "key-only",
})

_RECOVERY_REQUIRED_KEYS: tuple[str, ...] = (
    "method",
    "library_id",
    "library_type",
    "item_key",
    "attachment_key",
)

_OPENKB_POLICY_HINT_REQUIRED_KEYS: tuple[str, ...] = (
    "no_auth_url",
    "prefer_canonical_filename",
    "verification_required",
    "source_type",
)

_OPENKB_PREVIEW_SCHEMA_VERSION = "openkb-millefeuille-handoff-preview/v0.1"
_OPENKB_ACCEPTANCE_SUMMARY_SCHEMA_VERSION = (
    "openkb-millefeuille-acceptance-summary/v0.1"
)


def _is_generic_filename(filename: str) -> bool:
    x = filename.strip().lower()
    return x in _GENERIC_FILENAMES


_SENSITIVE_QUERY_PARAMS: frozenset[str] = frozenset({
    "key",
    "api_key",
    "token",
    "access_token",
    "authorization",
    "signature",
    "signed",
    "expires",
})

_CREDENTIAL_HEADER_RE = re.compile(r"(?i)^\s*(Authorization:|Bearer |Basic )")
_SESSION_COOKIE_RE = re.compile(r"(?i)(Cookie:|Set-Cookie:|session=|sessionid=)")
_LARGE_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{512,}={0,2}")


@dataclass
class ValidationReport:
    """Summary of OpenKB handoff row validation."""

    total_rows: int
    clean_rows: int
    failures: list[Exception] = field(default_factory=list)
    weak_verification_rows: list[dict] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.failures) == 0


def _raise_handoff_security(field_path: str, category: str) -> None:
    label = field_path or "(root)"
    raise HandoffSecurityError(
        f"Field '{label}' contains {category}"
    )


def _check_url_query_and_file_path(field_path: str, value: str) -> None:
    """Screen relative, scheme-less, and absolute URL-shaped strings."""
    parsed = urlparse(value)
    if parsed.query:
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
            name_lower = name.lower()
            if (
                name_lower in _SENSITIVE_QUERY_PARAMS
                or name_lower.startswith("x-amz-")
            ):
                _raise_handoff_security(field_path, "signed/authenticated URL")
    if parsed.path.endswith("/file") and parsed.query:
        _raise_handoff_security(
            field_path, "authenticated file endpoint URL"
        )


def _check_string_value(field_path: str, value: str) -> None:
    _check_url_query_and_file_path(field_path, value)

    if _CREDENTIAL_HEADER_RE.search(value):
        _raise_handoff_security(field_path, "credential header value")
    if _SESSION_COOKIE_RE.search(value):
        _raise_handoff_security(field_path, "session/cookie value")
    if value.startswith("data:application/pdf;base64,"):
        _raise_handoff_security(field_path, "embedded PDF base64")
    if len(value) > 512 and _LARGE_BASE64_RE.search(value):
        _raise_handoff_security(field_path, "large base64 blob")
    if "%PDF-" in value:
        _raise_handoff_security(field_path, "raw PDF magic bytes")


def _sanitize_walk(obj: object, field_path: str) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{field_path}.{key}" if field_path else str(key)
            _sanitize_walk(val, child)
    elif isinstance(obj, str):
        _check_string_value(field_path, obj)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            child = f"{field_path}[{idx}]" if field_path else f"[{idx}]"
            _sanitize_walk(item, child)


def sanitize_handoff_row(row: dict) -> dict:
    """Red-list scan for a handoff row dict; return unchanged if clean."""
    _sanitize_walk(row, "")
    return row


def _missing_nested_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _validate_recovery_object(recovery: object) -> list[str]:
    failures: list[str] = []
    if not isinstance(recovery, dict):
        return ["recovery: missing required field"]
    for key in _RECOVERY_REQUIRED_KEYS:
        if _missing_nested_value(recovery.get(key)):
            failures.append(f"recovery.{key}: missing required field")
    return failures


def _validate_openkb_policy_hints_object(policy: object) -> list[str]:
    failures: list[str] = []
    if not isinstance(policy, dict):
        return ["openkb_policy_hints: missing required field"]
    for key in _OPENKB_POLICY_HINT_REQUIRED_KEYS:
        if key not in policy:
            failures.append(f"openkb_policy_hints.{key}: missing required field")
    source_type = policy.get("source_type")
    if isinstance(source_type, str) and not source_type.strip():
        failures.append("openkb_policy_hints.source_type: missing required field")
    return failures


def build_openkb_handoff_rows(
    items: list[DiscoveredItem],
    zotero_client: ZoteroClient,
    config: OpenKBHandoffExportConfig,
) -> list[OpenKBHandoffRow]:
    """Build OpenKB handoff rows for PDF attachments on discovered items."""
    discovered_at = datetime.now(UTC).isoformat()
    library_id = zotero_client.credentials.library_id
    library_type = "user"

    rows: list[OpenKBHandoffRow] = []
    for item in items:
        for attachment in item.attachments:
            if not ZoteroClient._is_pdf_attachment(
                attachment.content_type, attachment.filename
            ):
                continue

            sha256 = attachment.sha256
            if sha256 is None and config.compute_sha256:
                pdf_bytes = zotero_client.download_pdf(item.key, attachment.key)
                sha256 = hashlib.sha256(pdf_bytes).hexdigest()

            if sha256 is not None:
                verification_strength = "full"
            elif attachment.md5 is not None:
                verification_strength = "hash-only"
            elif attachment.file_size_bytes is not None:
                verification_strength = "metadata-only"
            else:
                verification_strength = "key-only"

            recovery = {
                "method": "zotero_api_attachment",
                "library_id": library_id,
                "library_type": library_type,
                "item_key": item.key,
                "attachment_key": attachment.key,
            }
            openkb_policy_hints = {
                "no_auth_url": True,
                "prefer_canonical_filename": True,
                "verification_required": True,
                "source_type": "zotero",
            }

            item_type = (
                attachment.item_type if config.include_item_type else None
            )
            zotero_version = (
                attachment.zotero_version
                if config.include_zotero_version
                else None
            )

            rows.append(
                OpenKBHandoffRow(
                    schema_version="openkb-millefeuille-handoff/v0.1",
                    source_type="zotero",
                    discovered_at=discovered_at,
                    item_key=item.key,
                    item_type=item_type,
                    item_title=item.title,
                    citation_key=item.citation_key,
                    zotero_version=zotero_version,
                    attachment_key=attachment.key,
                    canonical_filename=attachment.filename,
                    content_type=attachment.content_type,
                    is_pdf=True,
                    file_size_bytes=attachment.file_size_bytes,
                    md5=attachment.md5,
                    sha256=sha256,
                    verification_strength=verification_strength,
                    recovery=recovery,
                    openkb_policy_hints=openkb_policy_hints,
                )
            )
    return rows


def validate_openkb_handoff_rows(
    rows: list[OpenKBHandoffRow],
    mode: str,
    source_items: list[DiscoveredItem] | None = None,
) -> ValidationReport:
    """Validate handoff rows; ``mode`` is reserved for future behaviour."""
    failures: list[Exception] = []
    weak_rows: list[dict] = []
    clean_count = 0

    source_attachment_map: dict[str, AttachmentInfo] = {}
    if source_items is not None:
        for item in source_items:
            for attachment in item.attachments:
                source_attachment_map[attachment.key] = attachment

    canonical_filename_seen: dict[str, set[str]] = {}

    for row in rows:
        row_failures: list[Exception] = []

        if not row.item_key:
            failures.append(
                AttachmentIdentityError("item_key: missing required field")
            )
            continue
        if not row.attachment_key:
            failures.append(
                AttachmentIdentityError(
                    "attachment_key: missing required field"
                )
            )
            continue

        if source_items is not None:
            attachment = source_attachment_map.get(row.attachment_key)
            if attachment is not None and attachment.link_mode == "linked_url":
                row_failures.append(
                    AttachmentIdentityError(
                        "link_mode: attachment uses linked URL mode"
                    )
                )

        if not (row.canonical_filename or "").strip():
            row_failures.append(
                AttachmentIdentityError(
                    "canonical_filename: missing required field"
                )
            )
        elif _is_generic_filename(row.canonical_filename):
            row_failures.append(
                AttachmentIdentityError(
                    "canonical_filename: generic filename not allowed"
                )
            )

        if (
            not row.verification_strength
            or row.verification_strength not in _VALID_VERIFICATION_STRENGTHS
        ):
            row_failures.append(
                AttachmentIdentityError(
                    "verification_strength: missing or invalid value"
                )
            )

        seen = canonical_filename_seen.setdefault(row.item_key, set())
        if row.canonical_filename in seen:
            row_failures.append(
                AttachmentIdentityError(
                    "canonical_filename: duplicate on same item"
                )
            )
        else:
            seen.add(row.canonical_filename)

        for req_field in (
            "schema_version",
            "source_type",
            "discovered_at",
        ):
            if not getattr(row, req_field, None):
                row_failures.append(
                    AttachmentIdentityError(
                        f"{req_field}: missing required field"
                    )
                )

        for msg in _validate_recovery_object(row.recovery):
            row_failures.append(AttachmentIdentityError(msg))
        for msg in _validate_openkb_policy_hints_object(
            row.openkb_policy_hints
        ):
            row_failures.append(AttachmentIdentityError(msg))

        try:
            sanitize_handoff_row(row.to_dict())
        except HandoffSecurityError as e:
            row_failures.append(e)

        failures.extend(row_failures)
        if not row_failures:
            clean_count += 1
            if row.verification_strength in _WEAK_VERIFICATION_STRENGTHS:
                weak_rows.append({
                    "item_key": row.item_key,
                    "attachment_key": row.attachment_key,
                    "canonical_filename": row.canonical_filename,
                    "verification_strength": row.verification_strength,
                })

    report = ValidationReport(
        total_rows=len(rows),
        clean_rows=clean_count,
        failures=failures,
        weak_verification_rows=weak_rows,
    )
    _logger.info(
        f"[{mode.upper()}] OpenKB handoff validation: {report.total_rows} "
        f"rows, {report.clean_rows} clean, {len(report.failures)} failure(s)."
    )
    return report


def verify_openkb_handoff_recovered_bytes(
    row: OpenKBHandoffRow,
    recovered_bytes: bytes,
) -> str:
    """Verify recovered attachment bytes against a handoff row SHA-256.

    This is the offline-safe intake guard for detecting source-version drift
    after handoff export: the caller already has recovered bytes in memory, and
    this function only compares hashes. It never serializes or logs payload
    bytes.
    """
    expected_sha256 = (row.sha256 or "").strip().lower()
    if not expected_sha256:
        raise AttachmentIdentityError(
            "sha256: required for recovered byte verification"
        )

    actual_sha256 = hashlib.sha256(recovered_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        context = (
            f"item_key={row.item_key!r} "
            f"attachment_key={row.attachment_key!r} "
            f"filename={row.canonical_filename!r}"
        )
        if row.zotero_version is not None:
            context = f"{context} zotero_version={row.zotero_version!r}"
        raise AttachmentIdentityError(
            "sha256: recovered bytes do not match handoff row "
            f"({context}; expected={expected_sha256}; actual={actual_sha256})"
        )
    return actual_sha256


def _copy_handoff_record(row: OpenKBHandoffRow | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, OpenKBHandoffRow):
        return row.to_dict()
    return dict(row)


def _copy_record(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record)


def _group_records_by_field(
    records: list[dict[str, Any]],
    field_name: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        value = record.get(field_name)
        if isinstance(value, str) and value.strip():
            grouped.setdefault(value, []).append(record)
    return grouped


def _skip_counts_by_event(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        event = str(record.get("event", "unknown"))
        counts[event] = counts.get(event, 0) + 1
    return dict(sorted(counts.items()))


def _project_duplicate_scan(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    projected: dict[str, Any] = {}
    for key in (
        "event",
        "document_slug",
        "canonical_filename",
        "scan_result",
        "matched_existing",
        "match_count",
        "verification_result",
    ):
        if key in record:
            projected[key] = record[key]
    return projected


def build_openkb_acceptance_summary(
    handoff_rows: list[OpenKBHandoffRow | dict[str, Any]],
    outcome_records: list[dict[str, Any]],
    duplicate_scan_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Join offline handoff, OpenKB outcome, skip, and duplicate-scan evidence.

    The summary is intentionally source-neutral and payload-free: callers pass
    already-materialized fixture or manifest rows, and this helper only compares
    record identity. It does not read Zotero, recover PDFs, call OCR providers,
    inspect OpenKB state, or write source packs.
    """
    handoff = [_copy_handoff_record(row) for row in handoff_rows]
    outcomes = [_copy_record(record) for record in outcome_records]
    duplicate_scans = [
        _copy_record(record) for record in (duplicate_scan_records or [])
    ]

    for record in [*handoff, *outcomes, *duplicate_scans]:
        sanitize_handoff_row(record)

    imports = [
        record for record in outcomes if record.get("event") == "openkb-added"
    ]
    skips = [
        record
        for record in outcomes
        if str(record.get("event", "")).startswith("skipped-")
    ]

    handoff_by_filename = _group_records_by_field(handoff, "canonical_filename")
    duplicate_scans_by_slug = _group_records_by_field(
        duplicate_scans, "document_slug"
    )

    joined_imports: list[dict[str, Any]] = []
    import_join_failures: list[dict[str, Any]] = []

    for imported in imports:
        filename = imported.get("canonical_filename")
        document_slug = imported.get("document_slug")
        handoff_matches = (
            handoff_by_filename.get(filename, [])
            if isinstance(filename, str)
            else []
        )
        duplicate_matches = (
            duplicate_scans_by_slug.get(document_slug, [])
            if isinstance(document_slug, str)
            else []
        )
        handoff_match = handoff_matches[0] if len(handoff_matches) == 1 else None
        duplicate_scan = (
            duplicate_matches[0] if len(duplicate_matches) == 1 else None
        )

        entry = {
            "canonical_filename": filename,
            "document_slug": document_slug,
            "source_type": imported.get("source_type"),
            "selected_route": imported.get("selected_route"),
            "verification_result": imported.get("verification_result"),
            "handoff": {
                "matched": handoff_match is not None,
                "match_count": len(handoff_matches),
            },
            "duplicate_scan": {
                "matched": duplicate_scan is not None,
                "match_count": len(duplicate_matches),
                "evidence": _project_duplicate_scan(duplicate_scan),
            },
        }
        if handoff_match is not None:
            entry["handoff"].update({
                "attachment_key": handoff_match.get("attachment_key"),
                "verification_strength": handoff_match.get(
                    "verification_strength"
                ),
                "sha256_present": handoff_match.get("sha256") is not None,
            })
        joined_imports.append(entry)

        if len(handoff_matches) != 1 or len(duplicate_matches) != 1:
            import_join_failures.append({
                "canonical_filename": filename,
                "document_slug": document_slug,
                "handoff_match_count": len(handoff_matches),
                "duplicate_scan_match_count": len(duplicate_matches),
            })

    import_filenames = {
        record["canonical_filename"]
        for record in imports
        if isinstance(record.get("canonical_filename"), str)
    }
    import_slugs = {
        record["document_slug"]
        for record in imports
        if isinstance(record.get("document_slug"), str)
    }
    unmatched_handoff_rows = [
        {
            "canonical_filename": record.get("canonical_filename"),
            "attachment_key": record.get("attachment_key"),
            "verification_strength": record.get("verification_strength"),
        }
        for record in handoff
        if record.get("canonical_filename") not in import_filenames
    ]
    unmatched_duplicate_scans = [
        {
            "document_slug": record.get("document_slug"),
            "canonical_filename": record.get("canonical_filename"),
            "scan_result": record.get("scan_result"),
        }
        for record in duplicate_scans
        if record.get("document_slug") not in import_slugs
    ]

    duplicate_scan_review_rows = [
        record
        for record in duplicate_scans
        if record.get("matched_existing")
        or record.get("match_count", 0) not in (0, None)
    ]
    status = (
        "pass"
        if not import_join_failures
        and not unmatched_handoff_rows
        and not unmatched_duplicate_scans
        and not duplicate_scan_review_rows
        else "needs-review"
    )
    summary = {
        "schema_version": _OPENKB_ACCEPTANCE_SUMMARY_SCHEMA_VERSION,
        "status": status,
        "counts": {
            "handoff_rows": len(handoff),
            "openkb_added": len(imports),
            "skipped_total": len(skips),
            "skipped_by_event": _skip_counts_by_event(skips),
            "duplicate_scans": len(duplicate_scans),
            "joined_imports": len(joined_imports),
            "import_join_failures": len(import_join_failures),
            "unmatched_handoff_rows": len(unmatched_handoff_rows),
            "unmatched_duplicate_scans": len(unmatched_duplicate_scans),
            "duplicate_scan_review_rows": len(duplicate_scan_review_rows),
        },
        "imports": joined_imports,
        "skips": skips,
        "unmatched": {
            "imports": import_join_failures,
            "handoff_rows": unmatched_handoff_rows,
            "duplicate_scans": unmatched_duplicate_scans,
        },
    }
    sanitize_handoff_row(summary)
    return summary


def log_openkb_weak_verification_warnings(
    logger: logging.Logger,
    weak_verification_rows: list[dict],
) -> None:
    """Log operator-visible warnings for clean rows with weak verification strength."""
    if not weak_verification_rows:
        return
    logger.warning(
        "[OPENKB HANDOFF] %d row(s) have weak verification strength:",
        len(weak_verification_rows),
    )
    for entry in weak_verification_rows[:10]:
        logger.warning(
            "  item_key=%s attachment_key=%s filename=%s strength=%s",
            entry["item_key"],
            entry["attachment_key"],
            entry["canonical_filename"],
            entry["verification_strength"],
        )
    if len(weak_verification_rows) > 10:
        logger.warning(
            "  ... and %d more row(s) with weak verification strength.",
            len(weak_verification_rows) - 10,
        )


def _redact_preview_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= 4:
        return "<redacted>"
    if len(text) <= 12:
        return f"{text[:2]}...{text[-2:]}"
    return f"{text[:6]}...{text[-4:]}"


def build_openkb_handoff_preview_rows(
    rows: list[OpenKBHandoffRow],
) -> list[dict[str, Any]]:
    """Build non-authoritative preview rows for dry-run inspection.

    Preview rows intentionally do not mirror the durable handoff schema. They
    preserve operator-visible shape and validation state while redacting exact
    Zotero identifiers and omitting recovery/hash values that downstream systems
    would treat as authoritative.
    """
    preview_rows: list[dict[str, Any]] = []
    for row in rows:
        preview_row = {
            "schema_version": _OPENKB_PREVIEW_SCHEMA_VERSION,
            "preview": True,
            "authoritative": False,
            "source_type": row.source_type,
            "discovered_at": row.discovered_at,
            "canonical_filename": row.canonical_filename,
            "content_type": row.content_type,
            "is_pdf": row.is_pdf,
            "file_size_bytes": row.file_size_bytes,
            "verification_strength": row.verification_strength,
            "identity": {
                "library_id": _redact_preview_identifier(
                    row.recovery.get("library_id")
                ),
                "library_type": row.recovery.get("library_type"),
                "item_key": _redact_preview_identifier(row.item_key),
                "attachment_key": _redact_preview_identifier(
                    row.attachment_key
                ),
                "item_type": row.item_type,
                "zotero_version": row.zotero_version,
            },
            "hashes": {
                "md5_present": row.md5 is not None,
                "sha256_present": row.sha256 is not None,
            },
            "recovery": {
                "method": row.recovery.get("method"),
                "available": True,
                "redacted": True,
            },
            "openkb_policy_hints": row.openkb_policy_hints,
            "omitted_fields": [
                "item_key",
                "attachment_key",
                "citation_key",
                "item_title",
                "md5",
                "sha256",
                "recovery.library_id",
                "recovery.item_key",
                "recovery.attachment_key",
            ],
        }
        sanitize_handoff_row(preview_row)
        preview_rows.append(preview_row)
    return preview_rows


def _write_jsonl_dicts(
    records: list[dict[str, Any]],
    jsonl_path: str,
    *,
    log_label: str,
) -> None:
    for record in records:
        sanitize_handoff_row(record)

    path = Path(jsonl_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            for record in records:
                line = json.dumps(record, ensure_ascii=False) + "\n"
                tmp.write(line)
            tmp.flush()
        Path(tmp_path).replace(path)
    finally:
        if tmp_path is not None:
            try:
                leftover = Path(tmp_path)
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass
    _logger.info(f"{log_label} written to {jsonl_path} ({len(records)} rows).")


def write_openkb_jsonl(
    rows: list[OpenKBHandoffRow],
    jsonl_path: str,
) -> None:
    """Write handoff rows to a UTF-8 JSONL file (atomic replace)."""
    records = [row.to_dict() for row in rows]
    _write_jsonl_dicts(
        records,
        jsonl_path,
        log_label="Handoff manifest",
    )


def write_openkb_preview_jsonl(
    rows: list[OpenKBHandoffRow],
    jsonl_path: str,
) -> None:
    """Write non-authoritative preview rows to a UTF-8 JSONL file."""
    _write_jsonl_dicts(
        build_openkb_handoff_preview_rows(rows),
        jsonl_path,
        log_label="Handoff preview",
    )


def build_export_records(
    items: list[DiscoveredItem],
    zotero_client: ZoteroClient,
) -> list[DiscoveredAttachmentExportRecord]:
    """Build export rows for PDF attachments on the given discovered items."""
    discovered_at = datetime.now(UTC).isoformat()
    library_id = zotero_client.credentials.library_id
    library_type = "user"

    records: list[DiscoveredAttachmentExportRecord] = []
    for item in items:
        for attachment in item.attachments:
            if not ZoteroClient._is_pdf_attachment(
                attachment.content_type, attachment.filename
            ):
                continue
            if not attachment.key:
                raise ValueError(
                    f"Attachment has no Zotero key (item_key={item.key!r}, "
                    f"filename={attachment.filename!r})"
                )

            filename_raw = attachment.filename
            if not isinstance(filename_raw, str):
                raise ValueError(
                    f"Filename fidelity check failed for item_key={item.key!r} "
                    f"attachment_key={attachment.key!r}: "
                    f"filename={filename_raw!r} is not a string "
                    "(Zotero API may have returned null or a non-text value). "
                    f"{_ZOTERO_RENAME_HINT}"
                )
            if not filename_raw.strip():
                raise ValueError(
                    f"Filename fidelity check failed for item_key={item.key!r} "
                    f"attachment_key={attachment.key!r}: "
                    f"filename={filename_raw!r} is empty or whitespace-only. "
                    f"{_ZOTERO_RENAME_HINT}"
                )
            if _is_generic_filename(filename_raw):
                raise ValueError(
                    f"Filename fidelity check failed for item_key={item.key!r} "
                    f"attachment_key={attachment.key!r}: "
                    f"filename={filename_raw!r} matches a known generic "
                    f"fallback pattern. {_ZOTERO_RENAME_HINT}"
                )

            zotero_uri_web = f"https://www.zotero.org/users/{library_id}/items/{item.key}"
            zotero_uri = zotero_uri_web
            zotero_uri_select = f"zotero://select/library/items/{item.key}"
            zotero_file_url = zotero_client.build_attachment_file_url(
                attachment.key, library_type
            )

            records.append(
                DiscoveredAttachmentExportRecord(
                    item_key=item.key,
                    attachment_key=attachment.key,
                    filename=filename_raw,
                    filename_source="zotero_attachment",
                    citation_key=item.citation_key,
                    zotero_uri=zotero_uri,
                    zotero_uri_web=zotero_uri_web,
                    zotero_uri_select=zotero_uri_select,
                    zotero_file_url=zotero_file_url,
                    discovered_at=discovered_at,
                    item_title=item.title,
                    library_id=library_id,
                    library_type=library_type,
                    content_type=attachment.content_type,
                    is_pdf=True,
                )
            )
    return records


def log_export_records(
    records: list[DiscoveredAttachmentExportRecord],
    logger: logging.Logger,
) -> None:
    """Log each export record and a short summary."""
    if not records:
        logger.info("No PDF attachments found — nothing to export.")
        return

    for rec in records:
        ck = rec.citation_key if rec.citation_key is not None else ""
        msg = (
            f"[DISCOVERY URL] item_key={rec.item_key}  "
            f"attachment_key={rec.attachment_key}\n"
            f"                filename={rec.filename}  citation_key={ck}\n"
            f"                zotero_uri={rec.zotero_uri}\n"
            f"                zotero_uri_web={rec.zotero_uri_web}\n"
            f"                zotero_uri_select={rec.zotero_uri_select}\n"
            f"                zotero_file_url={rec.zotero_file_url}"
        )
        logger.info(msg)
    logger.info(f"Exported {len(records)} attachment URL record(s).")


def write_manifest(
    records: list[DiscoveredAttachmentExportRecord],
    manifest_path: str,
) -> None:
    """Write export records to a UTF-8 JSON manifest file."""
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [record.to_dict() for record in records]
    content = json.dumps(data, indent=2, ensure_ascii=False)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(content)
            tmp.flush()
        Path(tmp_path).replace(path)
    finally:
        if tmp_path is not None:
            try:
                leftover = Path(tmp_path)
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass
    _logger.info(
        f"Manifest written to {manifest_path} ({len(records)} records)."
    )
