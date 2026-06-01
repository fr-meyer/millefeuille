"""Helpers for building and exporting discovered attachment URL records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlparse

from zotero_docai_pipeline.clients.exceptions import (
    AttachmentIdentityError,
    HandoffSecurityError,
)
from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
from zotero_docai_pipeline.domain.config import OpenKBHandoffExportConfig
from zotero_docai_pipeline.domain.models import (
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
})

_VALID_VERIFICATION_STRENGTHS: frozenset[str] = frozenset({
    "full",
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

            if attachment.md5 is not None:
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
                    schema_version="openkb-docai-handoff/v0.1",
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
                    sha256=None,
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

    report = ValidationReport(
        total_rows=len(rows),
        clean_rows=clean_count,
        failures=failures,
    )
    _logger.info(
        f"[{mode.upper()}] OpenKB handoff validation: {report.total_rows} "
        f"rows, {report.clean_rows} clean, {len(report.failures)} failure(s)."
    )
    return report


def write_openkb_jsonl(
    rows: list[OpenKBHandoffRow],
    jsonl_path: str,
) -> None:
    """Write handoff rows to a UTF-8 JSONL file (atomic replace)."""
    for row in rows:
        sanitize_handoff_row(row.to_dict())

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
            for row in rows:
                line = json.dumps(
                    row.to_dict(), ensure_ascii=False
                ) + "\n"
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
    _logger.info(
        f"Handoff manifest written to {jsonl_path} ({len(rows)} rows)."
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
