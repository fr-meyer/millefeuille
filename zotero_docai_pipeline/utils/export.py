"""Helpers for building and exporting discovered attachment URL records."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import tempfile

from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
from zotero_docai_pipeline.domain.models import (
    DiscoveredAttachmentExportRecord,
    DiscoveredItem,
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


def _is_generic_filename(filename: str) -> bool:
    x = filename.strip().lower()
    return x in _GENERIC_FILENAMES


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
