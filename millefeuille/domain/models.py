"""
Domain models for the Millefeuille.

This module defines the core data structures that represent the flow of
information through the OCR pipeline, from processing outcomes to extracted
content to Zotero note payloads.
These models provide type safety and clear documentation for the data transformations
that occur during document processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


@dataclass
class CreatorInfo:
    """Bibliographic creator (author or editor) information from a Zotero item."""

    creator_type: str
    """Creator role, e.g. 'author' or 'editor'."""

    full_name: str | None = None
    """Single-field name when first/last split is unavailable."""

    first_name: str | None = None
    """Given name of the creator."""

    last_name: str | None = None
    """Family name of the creator."""


@dataclass
class AttachmentInfo:
    """Metadata for a file attachment linked to a Zotero item."""

    key: str
    """Zotero attachment key."""

    filename: str
    """Original filename of the attachment."""

    content_type: str | None = None
    """MIME type of the attachment, e.g. 'application/pdf'."""

    link_mode: str | None = None
    """Zotero link mode, e.g. 'imported_file' or 'linked_url'."""

    file_size_bytes: int | None = None
    """File size in bytes from Zotero metadata, when available."""

    md5: str | None = None
    """MD5 checksum from Zotero metadata, when available."""

    sha256: str | None = None
    """SHA-256 checksum computed from attachment bytes, when available."""

    zotero_version: int | None = None
    """Zotero item version number, when available."""

    item_type: str | None = None
    """Parent item type from Zotero metadata, when available."""


@dataclass
class PaperMetadata:
    """Rich bibliographic metadata harvested from a Zotero item.

    Captures the most commonly needed fields for downstream analysis,
    summarisation, and tagging. The ``to_dict()`` helper serialises the
    dataclass to a JSON-friendly dict, omitting ``None`` scalars while
    always including list fields and ``author_count``.
    """

    item_type: str | None = None
    title: str | None = None
    abstract_note: str | None = None
    date: str | None = None
    year: int | None = None
    publication_title: str | None = None
    journal_abbreviation: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    issn: str | None = None
    isbn: str | None = None
    url: str | None = None
    language: str | None = None
    publisher: str | None = None
    place: str | None = None
    series: str | None = None
    series_title: str | None = None
    short_title: str | None = None
    rights: str | None = None
    extra: str | None = None
    citation_key: str | None = None
    num_pages: str | None = None
    author_string: str | None = None
    zotero_uri: str | None = None

    author_count: int = 0
    """Number of authors listed on the item."""

    authors: list[CreatorInfo] = field(default_factory=list)
    """Authors extracted from the item's creator list."""

    editors: list[CreatorInfo] = field(default_factory=list)
    """Editors extracted from the item's creator list."""

    tags: list[str] = field(default_factory=list)
    """Tags already present on the Zotero item."""

    attachments: list[AttachmentInfo] = field(default_factory=list)
    """File attachments linked to this item."""

    collections: list[str] | None = None
    """Zotero collection keys the item belongs to. ``None`` when not fetched."""

    def _creator_to_dict(self, creator: CreatorInfo) -> dict[str, Any]:
        d: dict[str, Any] = {"creator_type": creator.creator_type}
        if creator.full_name is not None:
            d["full_name"] = creator.full_name
        if creator.first_name is not None:
            d["first_name"] = creator.first_name
        if creator.last_name is not None:
            d["last_name"] = creator.last_name
        return d

    def _attachment_to_dict(self, att: AttachmentInfo) -> dict[str, Any]:
        d: dict[str, Any] = {"key": att.key, "filename": att.filename}
        if att.content_type is not None:
            d["content_type"] = att.content_type
        if att.link_mode is not None:
            d["link_mode"] = att.link_mode
        return d

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict, omitting ``None`` scalar fields."""
        result: dict[str, Any] = {}
        skipped_fields = {
            "author_count",
            "authors",
            "editors",
            "tags",
            "attachments",
            "collections",
        }
        for field_info in fields(self):
            if field_info.name in skipped_fields:
                continue
            val = getattr(self, field_info.name)
            if val is not None:
                result[field_info.name] = val

        result["author_count"] = self.author_count
        result["authors"] = [self._creator_to_dict(a) for a in self.authors]
        result["editors"] = [self._creator_to_dict(e) for e in self.editors]
        result["tags"] = self.tags
        result["attachments"] = [self._attachment_to_dict(a) for a in self.attachments]

        if self.collections is not None:
            result["collections"] = self.collections

        return result


@dataclass
class DiscoveredItem:
    """A Zotero library item that matched a discovery/filter query."""

    key: str
    """Zotero item key."""

    title: str
    """Item title."""

    tags: list[str]
    """Tags already present on the item."""

    attachments: list[AttachmentInfo]
    """File attachments linked to this item."""

    citation_key: str | None
    """Citation key (e.g. BibTeX key), if available."""

    paper_metadata: PaperMetadata
    """Full bibliographic metadata for downstream processing."""


@dataclass
class DiscoveryStats:
    """Aggregate statistics from a Zotero item discovery run."""

    matched_count: int
    """Number of items that matched the discovery criteria."""

    excluded_count: int
    """Total number of items excluded."""

    excluded_by_rule: dict[str, int]
    """Breakdown of exclusions by rule name."""


@dataclass
class ProcessingResult:
    """Represents the outcome of processing a single Zotero item.

    This model tracks the overall success status and detailed metrics for a single
    item processed by the pipeline, including PDF processing counts, page extraction
    statistics, and any errors encountered. Used for summary reporting and logging.
    """

    item_key: str
    """Zotero item key identifier. Uniquely identifies the item in the Zotero
    library."""

    item_title: str
    """Item title for logging and display purposes. Provides human-readable
    context for reports."""

    success: bool
    """Overall processing success status. True if all operations completed
    successfully, False otherwise."""

    pdfs_processed: int
    """Count of PDFs processed for this item. An item may have multiple
    attached PDFs."""

    pages_extracted: int
    """Total pages extracted across all PDFs for this item. Sum of all pages
    from all processed PDFs."""

    notes_created: int
    """Number of notes successfully created in Zotero. Each page typically
    results in one note."""

    errors: list[str]
    """List of error messages encountered during processing. Empty list if
    processing was successful."""

    processing_time: float
    """Processing duration in seconds. Includes time for PDF downloads, OCR
    processing, and note creation."""

    page_contents: dict[str, list[PageContent]] | None = None
    """Optional mapping of attachment_key to list of PageContent objects. Used
    for disk persistence of markdown content. Keys are Zotero attachment keys
    (unique per attachment) to avoid collisions when multiple attachments share
    the same filename. None if not collected."""

    tree_structures: dict[str, DocumentTree] | None = None
    """Optional mapping of attachment_key to DocumentTree objects. Used for disk
    persistence of tree structures. Keys are Zotero attachment keys (unique per
    attachment) to avoid collisions. None if not collected."""

    paper_metadata: PaperMetadata | None = None
    """Optional rich bibliographic metadata harvested from the Zotero item.
    Populated when metadata extraction is enabled. None if not collected."""


@dataclass
class TagAddingResult:
    """Per-item outcome of a Tag Adding operation.

    Kept separate from ProcessingResult to avoid mixing OCR and tag-adding concerns.
    """

    item_key: str
    """Zotero item key identifier."""

    item_title: str
    """Item title for logging and display."""

    matched: bool
    """Whether the item's citation key matched one of the configured citation keys."""

    tags_added: list[str]
    """Tags that were successfully added to the item."""

    tags_failed: list[str]
    """Tags that failed to be added to the item."""


@dataclass
class PageContent:
    """Represents extracted content from a single PDF page.

    This model is the intermediate format between Mistral OCR output and Zotero
    note creation. It contains all the structured data extracted from a single
    page, including markdown text, images, and tables. This format allows for
    processing and formatting before note creation.
    """

    page_number: int
    """1-indexed page number within the PDF. Used for note titles and organization."""

    markdown: str
    """Extracted markdown content from OCR. Contains the main text and
    structure of the page. This markdown is later converted to HTML when
    creating Zotero notes for rich text rendering."""

    has_content: bool
    """Flag indicating if page contains extractable content. False for blank
    or image-only pages without OCR text."""

    images: list[str] = field(default_factory=list)
    """List of base64-encoded images from the page. Optional field, empty
    list if no images were extracted."""

    tables: list[str] = field(default_factory=list)
    """List of extracted tables in markdown format. Optional field, empty
    list if no tables were found on the page."""


@dataclass
class UploadedDocument:
    """Represents metadata for an uploaded document awaiting OCR processing.

    This model tracks uploaded documents in batch processing workflows, storing
    both the OCR provider's document ID and contextual metadata for tracking.
    Used primarily for batch upload/processing coordination.
    """

    doc_id: str
    """Document identifier from OCR provider (e.g., Mistral file_id). Used
    for subsequent processing operations."""

    filename: str
    """Original PDF filename. Used for logging and error reporting."""

    upload_time: float
    """Unix timestamp when document was uploaded. Used for tracking and
    timeout detection."""

    item_key: str | None = None
    """Zotero item key that owns this document. Used for associating results
    back to source items. Optional for batch uploads without context."""

    attachment_key: str | None = None
    """Zotero attachment key for this specific PDF. Used for tracking which
    attachment was processed. Optional for batch uploads without context."""


@dataclass
class NotePayload:
    """Base class for Zotero note payloads ready for creation via the API.

    This model structures all the data needed to create a single note in Zotero from
    processed page content. The content field stores markdown format internally, which
    is automatically converted to HTML during note creation for rich text display in
    Zotero's TinyMCE editor. Notes are created in batches (up to 50 notes per API call
    per Zotero limits) to optimize API usage and processing throughput.

    This class serves as the base for specialized note types (figures, tables) while
    maintaining backward compatibility. All specialized note types inherit these base
    fields and add type-specific identifiers.
    """

    parent_item_key: str
    """Zotero item key that will be the parent of this note. The note will
    be attached as a child item."""

    pdf_filename: str
    """Source PDF filename for identification. Used in note titles and for
    tracking the source document."""

    content: str
    """Formatted note content in markdown format. This markdown is
    automatically converted to HTML when creating Zotero notes via
    ZoteroClient.create_notes_batch() for rich text display in Zotero's
    TinyMCE editor. The conversion supports GFM features including tables,
    code blocks, emphasis, lists, and base64 image data URIs."""

    title: str
    """Note title following multi-line format: "Page {page_number} -
    \\n{pdf_filename} -\\nOCR". Provides clear identification in Zotero."""

    page_number: int | None = None
    """Page number within the specific PDF. None for all-at-once mode, int
    for page-by-page mode."""


@dataclass
class TreeNode:
    """Represents a single node in a document tree structure.

    This model provides a recursive tree structure for representing document
    hierarchies, such as table of contents, section headings, or nested
    document structures. Each node can contain child nodes, creating a tree
    that represents the document's organizational structure. Nodes can
    optionally include text content, summaries, and page references for
    navigation and content extraction.

    The recursive structure allows for arbitrary nesting depth, supporting
    complex document hierarchies. The to_dict() method enables JSON
    serialization of the entire tree structure for persistence and API
    communication.
    """

    node_id: str
    """Unique identifier for this node within the document tree. Used for referencing
    and navigation within the tree structure."""

    title: str
    """Node title or heading text. Represents the section heading, chapter title, or
    other hierarchical label for this node."""

    page_index: int
    """0-indexed page number where this node appears in the document. Used for
    navigation and content location within the PDF."""

    text: str | None = None
    """Optional full text content associated with this node. When included, contains
    the complete text content for this section or node. Excluded by default to reduce
    storage size and improve performance."""

    summary: str | None = None
    """Optional summary or description of this node's content. Provides a condensed
    overview of the node's content without including the full text."""

    nodes: list[TreeNode] = field(default_factory=list)
    """List of child nodes, creating the recursive tree structure. Empty list for leaf
    nodes. Child nodes can themselves contain nested children, allowing for arbitrary
    depth hierarchies."""

    def to_dict(self) -> dict[str, Any]:
        """Convert this node and all child nodes to dictionary format for JSON
        serialization.

        Recursively converts the node and all descendants to a dictionary structure that
        can be serialized to JSON. This method handles the recursive nature of the tree
        by calling to_dict() on each child node.

        Returns:
            Dictionary representation of this node and all descendants, suitable for
            JSON serialization. Includes all fields that are not None, with child nodes
            recursively converted to dictionaries.
        """
        result: dict[str, Any] = {
            "node_id": self.node_id,
            "title": self.title,
            "page_index": self.page_index,
        }

        if self.text is not None:
            result["text"] = self.text

        if self.summary is not None:
            result["summary"] = self.summary

        if self.nodes:
            result["nodes"] = [node.to_dict() for node in self.nodes]

        return result


@dataclass
class DocumentTree:
    """Represents a complete document tree structure with metadata.

    This model encapsulates a document's hierarchical structure as a tree of
    TreeNode objects, along with document metadata (ID, name, description).
    The tree structure represents the document's organization, such as table
    of contents, section hierarchy, or nested document structure. This format
    enables navigation, content extraction, and structured representation of
    document organization.

    The tree is typically extracted during OCR processing when tree structure extraction
    is enabled in the configuration. The structure is saved to disk as JSON for later
    retrieval and use in document navigation and content organization workflows.
    """

    doc_id: str
    """Document identifier, typically matching the OCR provider's document ID or
    the source PDF filename. Used for associating the tree structure with the
    source document."""

    doc_name: str
    """Human-readable document name or filename. Used for display and identification
    purposes in tree structure outputs and navigation interfaces."""

    description: str | None = None
    """Optional document description or metadata. Provides additional context about
    the document's content, purpose, or structure."""

    nodes: list[TreeNode] = field(default_factory=list)
    """List of root-level tree nodes. These nodes form the top level of the document
    hierarchy, with each node potentially containing nested child nodes. Empty list
    indicates no tree structure was extracted or the document has no hierarchical
    organization."""

    def to_dict(self) -> dict[str, Any]:
        """Convert this document tree to dictionary format for JSON serialization.

        Converts the document tree, including all metadata and recursively converting
        all nodes to dictionaries. This method enables persistence of tree structures
        to disk as JSON files, typically saved as {pdf_name}_tree_structure.json in
        the item's output directory.

        Returns:
            Dictionary representation of the document tree, suitable for JSON
            serialization. Includes doc_id, doc_name, optional description, and
            recursively converted node structures.
        """
        result: dict[str, Any] = {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
        }

        if self.description is not None:
            result["description"] = self.description

        if self.nodes:
            result["nodes"] = [node.to_dict() for node in self.nodes]

        return result


@dataclass
class DiscoveredAttachmentExportRecord:
    """A single attachment URL row for export (e.g. JSON manifest)."""

    item_key: str
    """Zotero parent item key."""

    attachment_key: str
    """Zotero attachment key."""

    filename: str
    """Attachment filename as renamed by Zotero using the formula
    ``{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}``
    ``{{ title truncate="125" }}``.
    See https://www.zotero.org/support/file_renaming."""

    filename_source: str
    """Provenance of the filename. Always ``"zotero_attachment"`` for records
    assembled by this package."""

    citation_key: str | None
    """Citation key when available."""

    zotero_uri: str
    """Canonical Zotero web URL for the parent item (e.g.
    ``https://www.zotero.org/users/<id>/items/<key>``)."""

    zotero_uri_web: str
    """Explicit alias for the canonical web URL form; always identical to
    ``zotero_uri``."""

    zotero_uri_select: str
    """Zotero deep-link / local application URI for opening the item in the
    Zotero desktop client (e.g. ``zotero://select/library/items/<key>``)."""

    zotero_file_url: str
    """Zotero file URL for the attachment."""

    discovered_at: str
    """ISO or pipeline timestamp when the attachment was discovered."""

    item_title: str | None
    """Parent item title."""

    library_id: str | None
    """Zotero library id."""

    library_type: str | None
    """Zotero library type (e.g. user, group)."""

    content_type: str | None
    """Attachment MIME type."""

    is_pdf: bool | None
    """Whether the attachment is a PDF, when known."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise all fields to a dict, including ``None`` values."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class OpenKBHandoffRow:
    """Handoff row schema for openkb-millefeuille-handoff/v0.1.

    Parallel to :class:`DiscoveredAttachmentExportRecord` but purpose-built for
    OpenKB/Millefeuille JSONL handoff without authenticated URLs.
    """

    schema_version: str
    """Always ``\"openkb-millefeuille-handoff/v0.1\"``."""

    source_type: str
    """Always ``\"zotero\"``."""

    discovered_at: str
    """ISO-8601 UTC timestamp when the attachment was discovered."""

    item_key: str
    """Zotero parent item key."""

    attachment_key: str
    """Zotero attachment key."""

    canonical_filename: str
    """Canonical attachment filename for handoff."""

    is_pdf: bool
    """Whether the attachment is a PDF."""

    verification_strength: str
    """Identity verification level: ``full``, ``hash-only``, ``metadata-only``,
    or ``key-only``."""

    recovery: dict[str, str]
    """Recovery hints for downstream re-identification."""

    openkb_policy_hints: dict[str, object]
    """OpenKB policy hints for downstream processing."""

    item_type: str | None = None
    """Parent item type, when available."""

    item_title: str | None = None
    """Parent item title, when available."""

    citation_key: str | None = None
    """Citation key when available."""

    zotero_version: int | None = None
    """Zotero item version number, when available."""

    content_type: str | None = None
    """Attachment MIME type, when available."""

    file_size_bytes: int | None = None
    """File size in bytes from Zotero metadata, when available."""

    md5: str | None = None
    """MD5 checksum from Zotero metadata, when available."""

    sha256: str | None = None
    """SHA-256 checksum computed from attachment bytes, when available."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict, omitting ``None`` scalar fields."""
        result: dict[str, Any] = {}
        always_include = {"recovery", "openkb_policy_hints"}
        for field_info in fields(self):
            val = getattr(self, field_info.name)
            if val is None and field_info.name not in always_include:
                continue
            result[field_info.name] = val
        return result
