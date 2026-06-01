"""
Zotero API client wrapper for domain-specific operations.

This module provides a high-level interface to the Zotero API using PyZotero,
with domain-specific methods for tag-based item retrieval, PDF downloads,
batch note creation, and tag management. All methods include comprehensive
error handling and logging for production use.
"""

import html
import logging
import re
import time
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from pyzotero import zotero_errors
from pyzotero.zotero import Zotero

from zotero_docai_pipeline.clients.exceptions import (
    ZoteroAPIError,
    ZoteroAuthError,
    ZoteroItemNotFoundError,
)
from zotero_docai_pipeline.domain.config import (
    AuthQueryConfig,
    ConfigError,
    TagSelectionConfig,
)
from zotero_docai_pipeline.domain.markdown_converter import convert_markdown_to_html
from zotero_docai_pipeline.domain.models import (
    AttachmentInfo,
    CreatorInfo,
    DiscoveredItem,
    DiscoveryStats,
    NotePayload,
    PaperMetadata,
)
from zotero_docai_pipeline.utils.redaction import redact_url

logger = logging.getLogger(__name__)


class ZoteroClient:
    """Client for interacting with the Zotero API.

    This class wraps PyZotero's Zotero class to provide domain-specific methods
    for the Zotero Document AI pipeline, including tag-based item retrieval,
    PDF downloads, batch note creation, and tag management.

    Example:
        >>> from zotero_docai_pipeline.domain.config import AuthQueryConfig
        >>> from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
        >>>
        >>> config = AuthQueryConfig(
        ...     library_id="123456",
        ...     read_key="your_read_key_here",
        ... )
        >>> client = ZoteroClient(config)
        >>> items = client.get_items_by_tag("docai", "docai-processed")
    """

    @staticmethod
    def _is_pdf_attachment(
        content_type: str | None, filename: str | None
    ) -> bool:
        """Return True if attachment metadata represents a PDF."""
        normalized_content_type = (content_type or "").lower()
        normalized_filename = (filename or "").lower()
        return (
            normalized_content_type == "application/pdf"
            or normalized_filename.endswith(".pdf")
        )

    @staticmethod
    def _extract_citation_key(item_data: dict[str, Any]) -> str | None:
        """Extract citation key from item metadata.

        Native Zotero 8+ ``citationKey`` field is checked first; if absent or
        empty, the ``extra`` field is parsed for a ``Citation Key:`` line
        (Better BibTeX convention).  Returns ``None`` if neither source
        yields a value.
        """
        native = item_data.get("citationKey")
        if isinstance(native, str):
            native = native.strip()
            if native:
                return native

        extra = item_data.get("extra", "")
        if isinstance(extra, str) and extra:
            for line in extra.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("citation key:"):
                    value = stripped[len("citation key:"):].strip()
                    if value:
                        return value

        return None

    @staticmethod
    def _format_note_identifier(note: NotePayload) -> str:
        """Format note identifier for logging.

        Args:
            note: NotePayload object

        Returns:
            Formatted string like "filename - Page N" or "filename (All Pages)"
        """
        if note.page_number is not None:
            return f"{note.pdf_filename} - Page {note.page_number}"
        else:
            return f"{note.pdf_filename} (All Pages)"

    def __init__(self, config: AuthQueryConfig) -> None:
        """Initialize the Zotero client.

        Args:
            config: Zotero API credentials (library id, read key, optional write key).
        """
        self.config = config
        self.credentials: AuthQueryConfig = config
        self._zotero_read = Zotero(config.library_id, "user", config.read_key)
        if config.write_key is not None:
            self._zotero_write = Zotero(
                config.library_id, "user", config.write_key
            )
        else:
            self._zotero_write = None
        logger.info(f"Initialized ZoteroClient for library_id: {config.library_id}")

    def _require_write_client(self) -> Zotero:
        """Return the PyZotero client used for write operations, or raise."""
        if self._zotero_write is None:
            raise ConfigError(
                "ZOTERO_WRITE_KEY is required for write operations. "
                "Set the ZOTERO_WRITE_KEY environment variable."
            )
        return self._zotero_write

    def build_attachment_file_url(
        self, attachment_key: str, library_type: str | None = None
    ) -> str:
        """Build the Zotero API file URL for an attachment (no network I/O).

        Args:
            attachment_key: Zotero attachment item key.
            library_type: "user" (default) or "groups"; "group" is accepted and
                normalized to "groups".

        Returns:
            HTTPS URL for /users|groups/.../items/{key}/file.
        """
        lt = "user" if library_type is None else library_type.lower()
        if lt == "group":
            lt = "groups"
        if lt == "user":
            return (
                f"https://api.zotero.org/users/{self.config.library_id}/items/"
                f"{attachment_key}/file"
            )
        if lt == "groups":
            return (
                f"https://api.zotero.org/groups/{self.config.library_id}/items/"
                f"{attachment_key}/file"
            )
        raise ValueError(
            f"library_type must be 'user', 'group', or 'groups', got {library_type!r}"
        )

    def build_authenticated_attachment_url(
        self, attachment_key: str, library_type: str | None = None
    ) -> str:
        """Build a Zotero file URL with read-key query authentication (no network I/O).

        Returns the authenticated URL in memory only. Never log the return value
        directly — always pass through ``redact_url()`` first.

        Args:
            attachment_key: Zotero attachment item key.
            library_type: Same as :meth:`build_attachment_file_url`.

        Returns:
            HTTPS file URL including a ``key`` query parameter for the read API key.
        """
        plain_url = self.build_attachment_file_url(attachment_key, library_type)
        auth_url = f"{plain_url}?{urlencode({'key': self.credentials.read_key})}"
        logger.debug(
            "Built authenticated attachment URL: %s",
            redact_url(auth_url),
        )
        return auth_url

    def _fetch_items_for_tag(self, tag: str) -> dict[str, dict[str, Any]]:
        """Fetch all Zotero items matching a single tag.

        Returns a mapping of ``item_key → item_data`` (the ``"data"`` dict
        inside each raw API response entry).

        Args:
            tag: Zotero tag to query.

        Returns:
            Dict mapping item keys to their ``data`` dicts.

        Raises:
            ZoteroAPIError: If API communication fails.
            ZoteroAuthError: If authentication fails.
        """
        try:
            logger.debug(f"Fetching items for tag: {tag}")
            raw_items = self._zotero_read.everything(
                self._zotero_read.items(tag=tag)
            )

            result: dict[str, dict[str, Any]] = {}
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_data = item.get("data")
                if not isinstance(item_data, dict):
                    continue
                item_key = item.get("key")
                if item_key:
                    result[item_key] = item_data

            if len(result) > 100:
                logger.info(
                    f"Fetched {len(result)} items for tag '{tag}' (paginated)"
                )
            else:
                logger.debug(f"Fetched {len(result)} items for tag '{tag}'")
            return result

        except HTTPError as e:
            if e.code in (401, 403):
                error_msg = (
                    f"Authentication failed while fetching items with tag '{tag}'"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAuthError(error_msg, e) from e
            else:
                error_msg = (
                    f"API error while fetching items with tag '{tag}': HTTP {e.code}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAPIError(error_msg, e) from e
        except URLError as e:
            error_msg = f"Network error while fetching items with tag '{tag}'"
            logger.error(f"{error_msg}: {e}")
            raise ZoteroAPIError(error_msg, e) from e
        except Exception as e:
            error_msg = f"Unexpected error while fetching items with tag '{tag}'"
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise ZoteroAPIError(error_msg, e) from e

    def _extract_paper_metadata(
        self,
        item_data: dict[str, Any],
        include_abstract: bool,
        item_key: str | None = None,
    ) -> PaperMetadata:
        """Build a :class:`PaperMetadata` from a Zotero item ``data`` dict.

        This method never raises — on any unexpected error it logs a warning
        and returns a minimal :class:`PaperMetadata` instance.

        Args:
            item_data: The ``"data"`` dict from a Zotero API item response.
            include_abstract: Whether to populate ``abstract_note``.
            item_key: Optional Zotero item key, used to construct ``zotero_uri``.

        Returns:
            Fully populated :class:`PaperMetadata`.
        """
        try:
            # --- scalar fields ---
            item_type = item_data.get("itemType")
            title = item_data.get("title")
            short_title = item_data.get("shortTitle")
            doi = item_data.get("DOI")
            url = item_data.get("url")
            date = item_data.get("date")
            publication_title = item_data.get("publicationTitle")
            journal_abbreviation = item_data.get("journalAbbreviation")
            volume = item_data.get("volume")
            issue = item_data.get("issue")
            pages = item_data.get("pages")
            language = item_data.get("language")
            publisher = item_data.get("publisher")
            abstract_note = item_data.get("abstractNote") if include_abstract else None
            citation_key = ZoteroClient._extract_citation_key(item_data)

            # --- year parsing (best-effort) ---
            year: int | None = None
            date_str = item_data.get("date", "")
            if isinstance(date_str, str) and date_str:
                match = re.search(r"\b(\d{4})\b", date_str)
                if match:
                    try:
                        year = int(match.group(1))
                    except (ValueError, TypeError):
                        year = None

            # --- tags ---
            raw_tags = item_data.get("tags", [])
            tags = [t.get("tag", "") for t in raw_tags if isinstance(t, dict)]
            tags = [t for t in tags if t]

            # --- collections ---
            raw_collections = item_data.get("collections")
            collections = raw_collections if isinstance(raw_collections, list) else None

            # --- creators ---
            authors: list[CreatorInfo] = []
            editors: list[CreatorInfo] = []
            for creator in item_data.get("creators", []):
                if not isinstance(creator, dict):
                    continue
                creator_type = creator.get("creatorType", "")
                info = CreatorInfo(
                    creator_type=creator_type,
                    full_name=creator.get("name"),
                    first_name=creator.get("firstName"),
                    last_name=creator.get("lastName"),
                )
                if creator_type == "author":
                    authors.append(info)
                elif creator_type == "editor":
                    editors.append(info)

            # --- author_string ---
            def _display_name(c: CreatorInfo) -> str:
                if c.last_name:
                    return c.last_name
                if c.full_name:
                    return c.full_name
                if c.first_name:
                    return c.first_name
                return "Unknown"

            author_string: str | None = None
            if len(authors) == 1:
                author_string = _display_name(authors[0])
            elif len(authors) == 2:
                first = _display_name(authors[0])
                second = _display_name(authors[1])
                author_string = f"{first} and {second}"
            elif len(authors) >= 3:
                names = [_display_name(a) for a in authors]
                author_string = ", ".join(names[:-1]) + ", and " + names[-1]

            # --- zotero_uri ---
            zotero_uri: str | None = None
            if item_key and self.config.library_id:
                zotero_uri = (
                    f"https://www.zotero.org/users/"
                    f"{self.config.library_id}/items/{item_key}"
                )

            return PaperMetadata(
                item_type=item_type,
                title=title,
                short_title=short_title,
                doi=doi,
                url=url,
                date=date,
                year=year,
                publication_title=publication_title,
                journal_abbreviation=journal_abbreviation,
                volume=volume,
                issue=issue,
                pages=pages,
                language=language,
                publisher=publisher,
                abstract_note=abstract_note,
                citation_key=citation_key,
                tags=tags,
                collections=collections,
                authors=authors,
                editors=editors,
                author_count=len(authors),
                author_string=author_string,
                zotero_uri=zotero_uri,
            )
        except Exception as e:
            logger.warning(
                f"Failed to extract paper metadata for item_key={item_key}: {e}",
                exc_info=True,
            )
            return PaperMetadata()

    def get_items_by_tag(
        self, tag: str, exclude_tag: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve items by tag and filter out items with exclude_tag.

        Fetches all items with the specified tag, then filters out any items
        that also have the exclude_tag. For each item, retrieves child attachments
        to identify PDF files.

        Args:
            tag: Tag to filter items by. Items must have this tag to be included.
            exclude_tag: Optional tag to exclude. Items with this tag will be
                filtered out even if they have the target tag.

        Returns:
            List of dictionaries containing item metadata:
            - key: Zotero item key
            - title: Item title
            - tags: List of tag strings
            - attachments: List of attachment dicts with 'key' and 'filename'
            - citation_key: str | None — Citation key resolved from item metadata.
              Native Zotero 8+ ``citationKey`` field is preferred when present and
              non-empty; otherwise falls back to parsing the ``extra`` field for a
              ``Citation Key: <key>`` line (Better BibTeX convention). ``None`` if
              neither source yields a value.

        Raises:
            ZoteroAPIError: If API communication fails.
            ZoteroAuthError: If authentication fails.
        """
        items_map = self._fetch_items_for_tag(tag)
        items = [{"key": k, "data": v} for k, v in items_map.items()]

        # Filter out items with exclude_tag
        if exclude_tag:
            filtered_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_data_raw = item.get("data", {})
                if not isinstance(item_data_raw, dict):
                    continue
                item_tags = [
                    t.get("tag", "") for t in item_data_raw.get("tags", [])
                ]
                if exclude_tag not in item_tags:
                    filtered_items.append(item)
            items = filtered_items
            logger.debug(
                f"Filtered to {len(items)} items after excluding tag: {exclude_tag}"
            )

        # Enrich items with attachment information
        result = []

        for item in items:
            if not isinstance(item, dict):
                continue
            item_data_raw = item.get("data", {})
            if not isinstance(item_data_raw, dict):
                continue
            item_data = cast(dict[str, Any], item_data_raw)
            item_key = item.get("key")
            item_title = item_data.get("title", "Untitled")
            item_tags = [t.get("tag", "") for t in item_data.get("tags", [])]

            # Get child items (attachments)
            try:
                children = self._zotero_read.children(item_key)
                pdf_attachments = []
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_data_raw = child.get("data", {})
                    if not isinstance(child_data_raw, dict):
                        continue
                    child_data = cast(dict[str, Any], child_data_raw)
                    if (
                        child_data.get("itemType") == "attachment"
                        and child_data.get("contentType") == "application/pdf"
                    ):
                        pdf_attachments.append(
                            {
                                "key": child.get("key"),
                                "filename": child_data.get("filename") or "",
                            }
                        )
            except Exception as e:
                logger.warning(f"Failed to fetch children for item {item_key}: {e}")
                pdf_attachments = []

            citation_key = ZoteroClient._extract_citation_key(item_data)

            result.append(
                {
                    "key": item_key,
                    "title": item_title,
                    "tags": item_tags,
                    "attachments": pdf_attachments,
                    "citation_key": citation_key,
                }
            )

        logger.info(f"Retrieved {len(result)} items with tag '{tag}'")
        return result

    def get_items_by_selection(
        self,
        selection_cfg: TagSelectionConfig,
        include_abstract: bool,
    ) -> tuple[list[DiscoveredItem], DiscoveryStats]:
        """Discover Zotero items using structured tag selection rules.

        Fetches items matching the ``include`` rule, applies the ``exclude``
        rule and ``conflict_resolution`` policy, enriches each surviving item
        with PDF attachment info and full :class:`PaperMetadata`, and returns
        both the enriched list and aggregate discovery statistics.

        Args:
            selection_cfg: Tag selection rules (include/exclude/conflict).
            include_abstract: Whether to populate ``PaperMetadata.abstract_note``.

        Returns:
            A 2-tuple of ``(discovered_items, stats)``.

        Raises:
            ZoteroAPIError: If any Zotero API call fails.
            ZoteroAuthError: If authentication fails.
        """
        # ------------------------------------------------------------------
        # 1. Per-tag fetch (fail-fast)
        # ------------------------------------------------------------------
        per_tag_results: list[dict[str, dict[str, Any]]] = []
        for tag in selection_cfg.include.values:
            per_tag_results.append(self._fetch_items_for_tag(tag))

        # ------------------------------------------------------------------
        # 2. Set operation (union / intersection)
        # ------------------------------------------------------------------
        candidate_map: dict[str, dict[str, Any]] = {}
        if selection_cfg.include.operator == "or":
            for tag_result in per_tag_results:
                candidate_map.update(tag_result)
        else:  # "and"
            if per_tag_results:
                common_keys = set(per_tag_results[0].keys())
                for tag_result in per_tag_results[1:]:
                    common_keys &= set(tag_result.keys())
                for key in common_keys:
                    candidate_map[key] = per_tag_results[0][key]

        logger.info(
            f"Tag selection: {len(selection_cfg.include.values)} include tag(s) "
            f"(operator={selection_cfg.include.operator!r}), "
            f"{len(candidate_map)} candidate(s) before exclusion"
        )

        # ------------------------------------------------------------------
        # 3. Exclude rule evaluation
        # ------------------------------------------------------------------
        excluded_keys: set[str] = set()
        excluded_by_rule: dict[str, int] = {
            "exclude_rule": 0,
        }
        kept_by_conflict_include_wins = 0
        exclude_values = selection_cfg.exclude.values

        if exclude_values:
            for item_key, item_data in candidate_map.items():
                item_tags = {
                    t.get("tag", "")
                    for t in item_data.get("tags", [])
                    if isinstance(t, dict)
                }

                if selection_cfg.exclude.operator == "or":
                    matches_exclude = bool(item_tags & set(exclude_values))
                else:  # "and"
                    matches_exclude = set(exclude_values).issubset(item_tags)

                if matches_exclude:
                    if selection_cfg.conflict_resolution == "exclude_wins":
                        excluded_keys.add(item_key)
                        excluded_by_rule["exclude_rule"] += 1
                    else:  # include_wins
                        kept_by_conflict_include_wins += 1

        final_keys = [
            item_key for item_key in candidate_map
            if item_key not in excluded_keys
        ]

        logger.info(
            f"After exclusion: {len(excluded_keys)} excluded, "
            f"{len(final_keys)} remaining "
            f"(conflict_resolution={selection_cfg.conflict_resolution!r})"
        )
        if kept_by_conflict_include_wins > 0:
            logger.info(
                "Kept %d conflicting item(s) because "
                "conflict_resolution='include_wins'",
                kept_by_conflict_include_wins,
            )

        # ------------------------------------------------------------------
        # 4. Enrichment
        # ------------------------------------------------------------------
        discovered_items: list[DiscoveredItem] = []
        for item_key in final_keys:
            item_data = candidate_map[item_key]

            # Fetch PDF children
            attachments: list[AttachmentInfo] = []
            try:
                children = self._zotero_read.children(item_key)
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_data = child.get("data")
                    if not isinstance(child_data, dict):
                        continue
                    filename_raw = child_data.get("filename")
                    filename = (
                        filename_raw if isinstance(filename_raw, str) else None
                    )
                    if self._is_pdf_attachment(
                        child_data.get("contentType"), filename
                    ):
                        attachments.append(
                            AttachmentInfo(
                                key=child.get("key", ""),
                                filename=filename or "",
                                content_type=child_data.get("contentType"),
                                link_mode=child_data.get("linkMode"),
                                file_size_bytes=child_data.get("fileSize"),
                                md5=child_data.get("md5"),
                                zotero_version=item_data.get("version"),
                                item_type=item_data.get("itemType"),
                            )
                        )
            except HTTPError as e:
                logger.warning(
                    f"Failed to fetch children for item {item_key}: {e}",
                    exc_info=True,
                )
                if e.code in (401, 403):
                    error_msg = (
                        f"Authentication failed while fetching children for item "
                        f"'{item_key}'"
                    )
                    raise ZoteroAuthError(error_msg, e) from e

                error_msg = (
                    f"API error while fetching children for item '{item_key}': "
                    f"HTTP {e.code}"
                )
                raise ZoteroAPIError(error_msg, e) from e
            except URLError as e:
                logger.warning(
                    f"Failed to fetch children for item {item_key}: {e}",
                    exc_info=True,
                )
                error_msg = (
                    f"Network error while fetching children for item '{item_key}'"
                )
                raise ZoteroAPIError(error_msg, e) from e
            except Exception as e:
                logger.warning(
                    f"Failed to fetch children for item {item_key}: {e}",
                    exc_info=True,
                )
                error_msg = (
                    f"Unexpected error while fetching children for item '{item_key}'"
                )
                raise ZoteroAPIError(error_msg, e) from e

            paper_metadata = self._extract_paper_metadata(
                item_data, include_abstract, item_key=item_key,
            )
            paper_metadata.attachments = list(attachments)

            item_tags = [
                t.get("tag", "")
                for t in item_data.get("tags", [])
                if isinstance(t, dict)
            ]
            item_tags = [t for t in item_tags if t]

            discovered_items.append(
                DiscoveredItem(
                    key=item_key,
                    title=item_data.get("title", "Untitled"),
                    tags=item_tags,
                    attachments=attachments,
                    citation_key=paper_metadata.citation_key,
                    paper_metadata=paper_metadata,
                )
            )

        # ------------------------------------------------------------------
        # 5. Stats
        # ------------------------------------------------------------------
        stats = DiscoveryStats(
            matched_count=len(discovered_items),
            excluded_count=len(excluded_keys),
            excluded_by_rule=excluded_by_rule,
        )

        logger.info(
            f"Discovery complete: {stats.matched_count} matched, "
            f"{stats.excluded_count} excluded"
        )

        return discovered_items, stats

    def download_pdf(self, item_key: str, attachment_key: str) -> bytes:
        """Download a PDF attachment from Zotero.

        Retrieves the binary content of a PDF attachment associated with
        a Zotero item.

        Args:
            item_key: Zotero item key that owns the attachment.
            attachment_key: Zotero attachment key for the PDF file.

        Returns:
            Binary PDF content as bytes.

        Raises:
            ZoteroItemNotFoundError: If the attachment doesn't exist (404).
            ZoteroAPIError: If API communication fails.
            ZoteroAuthError: If authentication fails.
        """
        try:
            logger.debug(
                f"Downloading PDF: item_key={item_key}, attachment_key={attachment_key}"
            )
            pdf_bytes_raw = self._zotero_read.file(attachment_key)
            # Ensure return type is bytes
            if isinstance(pdf_bytes_raw, bytes):
                pdf_bytes = pdf_bytes_raw
            elif isinstance(pdf_bytes_raw, str):
                pdf_bytes = pdf_bytes_raw.encode("utf-8")
            else:
                pdf_bytes = bytes(pdf_bytes_raw)
            logger.info(f"Successfully downloaded PDF: attachment_key={attachment_key}")
            return pdf_bytes
        except HTTPError as e:
            if e.code == 404:
                error_msg = (
                    f"PDF attachment not found: attachment_key={attachment_key}, "
                    f"item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroItemNotFoundError(error_msg, e) from e
            elif e.code in (401, 403):
                error_msg = (
                    f"Authentication failed while downloading PDF: "
                    f"attachment_key={attachment_key}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAuthError(error_msg, e) from e
            else:
                error_msg = (
                    f"API error while downloading PDF: "
                    f"attachment_key={attachment_key}, HTTP {e.code}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAPIError(error_msg, e) from e
        except URLError as e:
            error_msg = (
                f"Network error while downloading PDF: attachment_key={attachment_key}"
            )
            logger.error(f"{error_msg}: {e}")
            raise ZoteroAPIError(error_msg, e) from e
        except Exception as e:
            error_msg = (
                f"Unexpected error while downloading PDF: "
                f"attachment_key={attachment_key}"
            )
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise ZoteroAPIError(error_msg, e) from e

    def create_notes_batch(
        self, notes: list[NotePayload], parent_key: str
    ) -> list[str]:
        """Create multiple notes in Zotero as a batch operation.

        Transforms NotePayload objects into Zotero note format and creates
        them as children of the specified parent item. Markdown content from
        NotePayload is automatically converted to HTML for Zotero's rich text
        editor (TinyMCE). If conversion fails, content is HTML-escaped and
        wrapped in <pre> tags as a fallback. This is an all-or-nothing
        operation: if any note fails to create, the entire batch fails.

        Args:
            notes: List of NotePayload objects to create as notes. Each note
                will be created as a child item of the parent. Markdown content
                from the payload is automatically converted to HTML for rich text
                display in Zotero.
            parent_key: Zotero item key that will be the parent of all notes.

        Returns:
            List of note keys for the successfully created notes.

        Raises:
            ValueError: If batch size exceeds 50 (Zotero API limit).
            ZoteroAPIError: If API call fails.
            ZoteroAuthError: If authentication fails.
        """
        if len(notes) > 50:
            error_msg = f"Batch size {len(notes)} exceeds Zotero API limit of 50"
            logger.error(error_msg)
            raise ValueError(error_msg)

        zwrite = self._require_write_client()
        try:
            # Transform NotePayload objects to Zotero note dicts
            note_dicts = []
            for note in notes:
                # Convert markdown content to HTML for Zotero's rich text editor
                try:
                    html_content = convert_markdown_to_html(note.content)

                    # Fallback to basic HTML escaping if conversion produces
                    # empty result
                    if not html_content or not html_content.strip():
                        logger.warning(
                            f"Markdown conversion produced empty HTML for note "
                            f"'{ZoteroClient._format_note_identifier(note)}', "
                            f"falling back to HTML escaping"
                        )
                        html_content = f"<pre>{html.escape(note.content)}</pre>"

                except Exception as e:
                    # Fallback to basic HTML escaping on conversion failure
                    logger.warning(
                        f"Failed to convert markdown to HTML for note "
                        f"'{ZoteroClient._format_note_identifier(note)}': {e}. "
                        f"Falling back to HTML escaping"
                    )
                    html_content = f"<pre>{html.escape(note.content)}</pre>"

                note_dict = {
                    "itemType": "note",
                    "note": html_content,  # Use converted HTML instead of raw markdown
                    "parentItem": parent_key,
                    "tags": [],
                }
                note_dicts.append(note_dict)

            logger.debug(
                f"Creating batch of {len(note_dicts)} notes for parent_key={parent_key}"
            )
            result = zwrite.create_items(note_dicts, parentid=parent_key)

            # Check for partial failures in batch operation
            failed = result.get("failed", {})
            if failed:
                # Extract successful note keys for rollback
                successful = result.get("successful", {})
                successful_keys = []

                if isinstance(successful, dict):
                    for idx in successful:
                        created_item = successful[idx]
                        if isinstance(created_item, dict) and "key" in created_item:
                            successful_keys.append(created_item["key"])
                        elif isinstance(created_item, list) and len(created_item) > 0:
                            item_data = (
                                created_item[0]
                                if isinstance(created_item[0], dict)
                                else created_item
                            )
                            if isinstance(item_data, dict) and "key" in item_data:
                                successful_keys.append(item_data["key"])

                # Log successful keys before rollback
                if successful_keys:
                    successful_titles = [
                        ZoteroClient._format_note_identifier(notes[int(idx)])
                        for idx in successful
                        if isinstance(idx, (str, int)) and int(idx) < len(notes)
                    ]
                    logger.warning(
                        f"Rolling back {len(successful_keys)} successfully "
                        f"created notes: {', '.join(successful_titles)}"
                    )

                    # Attempt rollback
                    try:
                        self.delete_notes(successful_keys)
                    except Exception as rollback_error:
                        logger.error(
                            f"Rollback failed for {len(successful_keys)} notes: "
                            f"{rollback_error}",
                            exc_info=True,
                        )

                # Map failed indices to note titles for error message
                note_titles = [
                    ZoteroClient._format_note_identifier(note) for note in notes
                ]
                failed_titles = []
                for idx in failed:
                    try:
                        idx_int = int(idx) if isinstance(idx, str) else idx
                        if 0 <= idx_int < len(note_titles):
                            failed_titles.append(note_titles[idx_int])
                    except (ValueError, TypeError):
                        continue

                error_msg = (
                    f"Batch note creation failed for {len(failed)} notes "
                    f"out of {len(note_dicts)}"
                )
                logger.error(f"{error_msg} for parent_key={parent_key}")
                logger.error(f"Failed note titles: {', '.join(failed_titles)}")
                raise ZoteroAPIError(error_msg)

            # Extract note keys from successful creation result
            # PyZotero's create_items returns a dict with 'successful' and 'failed' keys
            # 'successful' contains a dict mapping indices to created item data
            note_keys = []
            successful = result.get("successful", {})

            if isinstance(successful, dict):
                # Most common case: successful is a dict mapping indices to item data
                for idx in sorted(
                    successful.keys(),
                    key=lambda x: (
                        int(x)
                        if isinstance(x, str) and x.isdigit()
                        else (x if isinstance(x, int) else 0)
                    ),
                ):
                    created_item = successful[idx]
                    if isinstance(created_item, dict) and "key" in created_item:
                        note_keys.append(created_item["key"])
                    elif isinstance(created_item, list) and len(created_item) > 0:
                        # Handle case where item data is nested in a list
                        item_data = (
                            created_item[0]
                            if isinstance(created_item[0], dict)
                            else created_item
                        )
                        if isinstance(item_data, dict) and "key" in item_data:
                            note_keys.append(item_data["key"])
            elif isinstance(successful, list):
                # Fallback: successful is a list of created items
                for item in successful:
                    if isinstance(item, dict) and "key" in item:
                        note_keys.append(item["key"])

            # Validate that we extracted the expected number of keys
            if len(note_keys) != len(note_dicts):
                logger.warning(
                    f"Extracted {len(note_keys)} note keys but expected "
                    f"{len(note_dicts)}. Result structure: "
                    f"{type(result).__name__}, successful type: "
                    f"{type(successful).__name__}"
                )

            note_titles = [ZoteroClient._format_note_identifier(note) for note in notes]
            logger.info(
                f"Successfully created {len(note_dicts)} notes for "
                f"parent_key={parent_key}"
            )
            logger.debug(f"Note titles: {', '.join(note_titles)}")
            logger.debug(f"Created note keys: {note_keys}")

            return note_keys

        except HTTPError as e:
            note_titles = [ZoteroClient._format_note_identifier(note) for note in notes]
            if e.code in (401, 403):
                error_msg = (
                    f"Authentication failed while creating notes batch for "
                    f"parent_key={parent_key}"
                )
                logger.error(f"{error_msg}: {e}")
                logger.error(f"Failed note titles: {', '.join(note_titles)}")
                raise ZoteroAuthError(error_msg, e) from e
            else:
                error_msg = (
                    f"API error while creating notes batch for "
                    f"parent_key={parent_key}: HTTP {e.code}"
                )
                logger.error(f"{error_msg}: {e}")
                logger.error(f"Failed note titles: {', '.join(note_titles)}")
                raise ZoteroAPIError(error_msg, e) from e
        except URLError as e:
            note_titles = [ZoteroClient._format_note_identifier(note) for note in notes]
            error_msg = (
                f"Network error while creating notes batch for parent_key={parent_key}"
            )
            logger.error(f"{error_msg}: {e}")
            logger.error(f"Failed note titles: {', '.join(note_titles)}")
            raise ZoteroAPIError(error_msg, e) from e
        except Exception as e:
            note_titles = [ZoteroClient._format_note_identifier(note) for note in notes]
            error_msg = (
                f"Unexpected error while creating notes batch for "
                f"parent_key={parent_key}"
            )
            logger.error(f"{error_msg}: {e}", exc_info=True)
            logger.error(f"Failed note titles: {', '.join(note_titles)}")
            raise ZoteroAPIError(error_msg, e) from e

    def delete_notes(self, note_keys: list[str]) -> None:
        """Delete multiple notes from Zotero by their keys.

        This method is used for rollback operations when batch note creation
        fails partway through. It attempts to delete all specified notes,
        logging warnings for any that fail to delete (but not raising exceptions
        to allow cleanup to continue).

        Args:
            note_keys: List of Zotero note keys to delete.

        Raises:
            ZoteroAPIError: If API communication fails (but individual note
                deletion failures are logged as warnings, not raised).
        """
        if not note_keys:
            return

        zw = self._require_write_client()
        logger.debug(f"Deleting {len(note_keys)} notes for rollback")
        deleted_count = 0
        failed_keys = []

        for note_key in note_keys:
            try:
                zw.delete_item(note_key)
                deleted_count += 1
                logger.debug(f"Deleted note: {note_key}")
            except HTTPError as e:
                if e.code == 404:
                    # Note already deleted or doesn't exist - not an error for rollback
                    logger.debug(f"Note {note_key} not found (may already be deleted)")
                    deleted_count += 1
                elif e.code in (401, 403):
                    error_msg = f"Authentication failed while deleting note {note_key}"
                    logger.warning(f"{error_msg}: {e}")
                    failed_keys.append(note_key)
                else:
                    error_msg = (
                        f"API error while deleting note {note_key}: HTTP {e.code}"
                    )
                    logger.warning(f"{error_msg}: {e}")
                    failed_keys.append(note_key)
            except URLError as e:
                error_msg = f"Network error while deleting note {note_key}"
                logger.warning(f"{error_msg}: {e}")
                failed_keys.append(note_key)
            except Exception as e:
                error_msg = f"Unexpected error while deleting note {note_key}"
                logger.warning(f"{error_msg}: {e}", exc_info=True)
                failed_keys.append(note_key)

        if failed_keys:
            logger.warning(
                f"Failed to delete {len(failed_keys)} notes during rollback: "
                f"{failed_keys}"
            )
        else:
            logger.info(f"Successfully deleted {deleted_count} notes during rollback")

    def add_tag(self, item_key: str, tag: str) -> None:
        """Add a tag to a Zotero item.

        Fetches the item, adds the specified tag, and updates it via the API.

        Args:
            item_key: Zotero item key to add the tag to.
            tag: Tag name to add.

        Raises:
            ZoteroAPIError: If API call fails.
            ZoteroAuthError: If authentication fails.
            ZoteroItemNotFoundError: If the item doesn't exist (404).
        """
        zw = self._require_write_client()
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    f"Adding tag '{tag}' to item_key={item_key} (attempt {attempt})"
                )

                # Fetch item immediately before update to get latest version
                item = zw.item(item_key)

                # Check if tag already exists
                if not isinstance(item, dict):
                    raise ZoteroAPIError(
                        f"Invalid item structure for item_key={item_key}"
                    )
                item_data_raw = item.get("data", {})
                if not isinstance(item_data_raw, dict):
                    raise ZoteroAPIError(
                        f"Invalid item data structure for item_key={item_key}"
                    )
                item_data = cast(dict[str, Any], item_data_raw)
                item_tags = [
                    t.get("tag", "") if isinstance(t, dict) else t
                    for t in item_data.get("tags", [])
                ]
                if tag in item_tags:
                    logger.info(
                        f"Tag '{tag}' already exists on item_key={item_key}, "
                        f"skipping update"
                    )
                    return

                # Add tag to the item (modifies item in-place, preserves version)
                # Don't re-fetch - update immediately with the version we have
                # If version changed, the 412 error will trigger a retry with
                # fresh fetch
                zw.add_tags(item, tag)

                # Update immediately - if version changed, we'll get 412 and retry
                zw.update_item(item)
                logger.info(f"Successfully added tag '{tag}' to item_key={item_key}")
                return

            except zotero_errors.PreConditionFailedError as e:
                # Even if we got 412, the tag might have been added anyway
                # Verify by fetching the item and checking if tag exists
                try:
                    time.sleep(0.1)  # Small delay to allow Zotero to process
                    verify_item = zw.item(item_key)
                    if not isinstance(verify_item, dict):
                        raise ZoteroAPIError(
                            f"Invalid item structure for item_key={item_key}"
                        )
                    verify_item_data_raw = verify_item.get("data", {})
                    if not isinstance(verify_item_data_raw, dict):
                        raise ZoteroAPIError(
                            f"Invalid item data structure for item_key={item_key}"
                        )
                    verify_item_data = cast(dict[str, Any], verify_item_data_raw)
                    verify_tags = [
                        t.get("tag", "") if isinstance(t, dict) else t
                        for t in verify_item_data.get("tags", [])
                    ]

                    if tag in verify_tags:
                        # Tag was added despite 412 error - consider it success
                        logger.info(
                            f"Tag '{tag}' was successfully added to "
                            f"item_key={item_key} despite version conflict "
                            f"(attempt {attempt})"
                        )
                        return
                except Exception as verify_error:
                    # Verification failed, but continue with retry logic
                    logger.debug(
                        f"Failed to verify tag after 412 error: {verify_error}"
                    )

                if attempt == max_retries:
                    # Final attempt failed, raise error
                    error_msg = (
                        f"Failed to add tag '{tag}' to item_key={item_key} "
                        f"after {max_retries} retry attempts"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAPIError(error_msg, e) from e

                # Exponential backoff before retry (longer delays to allow
                # version to stabilize)
                delay = 0.5 * (2 ** (attempt - 1))  # 0.5s, 1.0s, 2.0s, 4.0s
                logger.debug(
                    f"Version conflict on attempt {attempt}, "
                    f"waiting {delay}s before retry"
                )
                time.sleep(delay)
                continue  # Retry the loop from the beginning (fresh fetch)

            except HTTPError as e:
                # Non-retryable HTTP errors - break out and raise immediately
                if e.code == 404:
                    error_msg = (
                        f"Item not found while adding tag: item_key={item_key}, "
                        f"tag={tag}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroItemNotFoundError(error_msg, e) from e
                elif e.code in (401, 403):
                    error_msg = (
                        f"Authentication failed while adding tag '{tag}' to "
                        f"item_key={item_key}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAuthError(error_msg, e) from e
                else:
                    error_msg = (
                        f"API error while adding tag '{tag}' to "
                        f"item_key={item_key}: HTTP {e.code}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAPIError(error_msg, e) from e
            except URLError as e:
                # Network errors - break out and raise immediately
                error_msg = (
                    f"Network error while adding tag '{tag}' to item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAPIError(error_msg, e) from e
            except Exception as e:
                # Other unexpected errors - break out and raise immediately
                error_msg = (
                    f"Unexpected error while adding tag '{tag}' to item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}", exc_info=True)
                raise ZoteroAPIError(error_msg, e) from e

    def set_tags(self, item_key: str, tags: list[str]) -> None:
        """Replace all tags on a Zotero item with the given list.

        Performs a strict wipe: the item's entire ``tags`` array is overwritten
        with ``tags``.  This is destructive — all prior tags (including
        system/pipeline/manual tags) are removed.

        Uses retry-on-412 logic identical to :meth:`add_tag` (max 5 attempts,
        exponential backoff ``0.5 * 2^(attempt-1)``).  After a 412 response
        the current tag set is re-fetched and compared (normalized, order-
        independent) against ``tags``; if they match the call is treated as
        successful.

        Args:
            item_key: Zotero item key whose tags will be replaced.
            tags: Full list of tag strings to set on the item.

        Raises:
            ZoteroAPIError: If API call fails after all retries.
            ZoteroAuthError: If authentication fails.
            ZoteroItemNotFoundError: If the item doesn't exist (404).
        """
        zw = self._require_write_client()
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    f"Setting tags on item_key={item_key} "
                    f"(attempt {attempt}, tags={tags})"
                )

                item = zw.item(item_key)

                if not isinstance(item, dict):
                    raise ZoteroAPIError(
                        f"Invalid item structure for item_key={item_key}"
                    )
                item_data_raw = item.get("data", {})
                if not isinstance(item_data_raw, dict):
                    raise ZoteroAPIError(
                        f"Invalid item data structure for item_key={item_key}"
                    )
                item_data = cast(dict[str, Any], item_data_raw)

                item_data["tags"] = [{"tag": t} for t in tags]

                zw.update_item(item)
                logger.info(
                    f"Successfully set tags {tags} on item_key={item_key}"
                )
                return

            except zotero_errors.PreConditionFailedError as e:
                try:
                    time.sleep(0.1)
                    verify_item = zw.item(item_key)
                    if not isinstance(verify_item, dict):
                        raise ZoteroAPIError(
                            f"Invalid item structure for item_key={item_key}"
                        )
                    verify_item_data_raw = verify_item.get("data", {})
                    if not isinstance(verify_item_data_raw, dict):
                        raise ZoteroAPIError(
                            f"Invalid item data structure for item_key={item_key}"
                        )
                    verify_item_data = cast(dict[str, Any], verify_item_data_raw)
                    verify_tags = {
                        t.get("tag", "").strip()
                        for t in verify_item_data.get("tags", [])
                        if isinstance(t, dict)
                    }
                    intended_tags = {t.strip() for t in tags}

                    if verify_tags == intended_tags:
                        logger.info(
                            f"Tags on item_key={item_key} match intended set "
                            f"despite version conflict (attempt {attempt})"
                        )
                        return
                except Exception as verify_error:
                    logger.debug(
                        f"Failed to verify tags after 412 error: {verify_error}"
                    )

                if attempt == max_retries:
                    error_msg = (
                        f"Failed to set tags on item_key={item_key} "
                        f"after {max_retries} retry attempts"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAPIError(error_msg, e) from e

                delay = 0.5 * (2 ** (attempt - 1))
                logger.debug(
                    f"Version conflict on attempt {attempt}, "
                    f"waiting {delay}s before retry"
                )
                time.sleep(delay)
                continue

            except HTTPError as e:
                if e.code == 404:
                    error_msg = (
                        f"Item not found while setting tags: "
                        f"item_key={item_key}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroItemNotFoundError(error_msg, e) from e
                elif e.code in (401, 403):
                    error_msg = (
                        f"Authentication failed while setting tags on "
                        f"item_key={item_key}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAuthError(error_msg, e) from e
                else:
                    error_msg = (
                        f"API error while setting tags on "
                        f"item_key={item_key}: HTTP {e.code}"
                    )
                    logger.error(f"{error_msg}: {e}")
                    raise ZoteroAPIError(error_msg, e) from e
            except URLError as e:
                error_msg = (
                    f"Network error while setting tags on item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAPIError(error_msg, e) from e
            except Exception as e:
                error_msg = (
                    f"Unexpected error while setting tags on "
                    f"item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}", exc_info=True)
                raise ZoteroAPIError(error_msg, e) from e

    def remove_tag(self, item_key: str, tag: str) -> None:
        """Remove a tag from a Zotero item.

        Fetches the item, removes the specified tag from its tags list,
        and updates it via the API. If the tag is already absent, this
        method is a no-op and does not call the update API.

        Args:
            item_key: Zotero item key to remove the tag from.
            tag: Tag name to remove.

        Raises:
            ZoteroAPIError: If API call fails.
            ZoteroAuthError: If authentication fails.
            ZoteroItemNotFoundError: If the item doesn't exist (404).
        """
        zw = self._require_write_client()
        try:
            logger.debug(f"Removing tag '{tag}' from item_key={item_key}")
            item = zw.item(item_key)

            # Manually remove tag from item['data']['tags'] list
            if not isinstance(item, dict):
                raise ZoteroAPIError(f"Invalid item structure for item_key={item_key}")
            item_data_raw = item.get("data", {})
            if not isinstance(item_data_raw, dict):
                raise ZoteroAPIError(
                    f"Invalid item data structure for item_key={item_key}"
                )
            item_data = cast(dict[str, Any], item_data_raw)
            item_tags = [
                t.get("tag", "") if isinstance(t, dict) else t
                for t in item_data.get("tags", [])
            ]
            if tag not in item_tags:
                logger.info(
                    f"Tag '{tag}' already absent from "
                    f"item_key={item_key}, skipping update"
                )
                return

            tags = item_data.get("tags", [])
            item_data["tags"] = [t for t in tags if t.get("tag") != tag]

            zw.update_item(item)
            logger.info(f"Successfully removed tag '{tag}' from item_key={item_key}")
        except zotero_errors.PreConditionFailedError as e:
            # Handle version conflict: retry with exponential backoff (up to 3 attempts)
            max_retries = 3
            retry_delay = 0.2  # 200ms initial delay
            last_error = e

            for attempt in range(1, max_retries + 1):
                logger.debug(
                    f"Version conflict detected for item_key={item_key}, "
                    f"retry attempt {attempt}/{max_retries}"
                )
                try:
                    # Small delay before retry to allow concurrent operations
                    # to complete
                    if attempt > 1:
                        time.sleep(
                            retry_delay * attempt
                        )  # Exponential backoff: 0.2s, 0.4s, 0.6s

                    # Re-fetch item to get latest version
                    item = zw.item(item_key)

                    # Check if tag already removed (may have been removed by
                    # another process)
                    if not isinstance(item, dict):
                        raise ZoteroAPIError(
                            f"Invalid item structure for item_key={item_key}"
                        )
                    item_data_raw = item.get("data", {})
                    if not isinstance(item_data_raw, dict):
                        raise ZoteroAPIError(
                            f"Invalid item data structure for item_key={item_key}"
                        )
                    item_data = cast(dict[str, Any], item_data_raw)
                    item_tags = [
                        t.get("tag", "") if isinstance(t, dict) else t
                        for t in item_data.get("tags", [])
                    ]
                    if tag not in item_tags:
                        logger.info(
                            f"Tag '{tag}' already removed from "
                            f"item_key={item_key}, skipping update"
                        )
                        return

                    # Manually remove tag from item['data']['tags'] list
                    # item is already verified as dict above, but reuse
                    # item_data_raw from above check to avoid redundant access
                    item_data = cast(dict[str, Any], item_data_raw)
                    tags = item_data.get("tags", [])
                    item_data["tags"] = [t for t in tags if t.get("tag") != tag]

                    # Update with fresh version
                    zw.update_item(item)
                    logger.info(
                        f"Successfully removed tag '{tag}' from "
                        f"item_key={item_key} after version conflict retry "
                        f"(attempt {attempt})"
                    )
                    return
                except zotero_errors.PreConditionFailedError as retry_error:
                    last_error = retry_error
                    if attempt == max_retries:
                        # Final attempt failed, raise error
                        error_msg = (
                            f"Failed to remove tag '{tag}' from "
                            f"item_key={item_key} after {max_retries} retry "
                            f"attempts"
                        )
                        logger.error(f"{error_msg}: {last_error}")
                        raise ZoteroAPIError(error_msg, last_error) from last_error
                    # Continue to next retry attempt
                    continue
                except Exception as retry_error:
                    error_msg = (
                        f"Failed to remove tag '{tag}' from "
                        f"item_key={item_key} after version conflict retry "
                        f"(attempt {attempt})"
                    )
                    logger.error(f"{error_msg}: {retry_error}")
                    raise ZoteroAPIError(error_msg, retry_error) from retry_error
        except HTTPError as e:
            if e.code == 404:
                error_msg = (
                    f"Item not found while removing tag: item_key={item_key}, tag={tag}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroItemNotFoundError(error_msg, e) from e
            elif e.code in (401, 403):
                error_msg = (
                    f"Authentication failed while removing tag '{tag}' from "
                    f"item_key={item_key}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAuthError(error_msg, e) from e
            else:
                error_msg = (
                    f"API error while removing tag '{tag}' from "
                    f"item_key={item_key}: HTTP {e.code}"
                )
                logger.error(f"{error_msg}: {e}")
                raise ZoteroAPIError(error_msg, e) from e
        except URLError as e:
            error_msg = (
                f"Network error while removing tag '{tag}' from item_key={item_key}"
            )
            logger.error(f"{error_msg}: {e}")
            raise ZoteroAPIError(error_msg, e) from e
        except Exception as e:
            error_msg = (
                f"Unexpected error while removing tag '{tag}' from item_key={item_key}"
            )
            logger.error(f"{error_msg}: {e}", exc_info=True)
            raise ZoteroAPIError(error_msg, e) from e
