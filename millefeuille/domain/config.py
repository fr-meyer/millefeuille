"""
Configuration dataclasses for the Millefeuille.

This module defines type-safe configuration schemas using Python dataclasses.
These schemas are registered with Hydra to enable validation and IDE autocomplete
support for configuration values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hydra.core.config_store import ConfigStore


class ConfigError(Exception):
    """Configuration error for the Millefeuille.

    Raised when configuration values are invalid or inconsistent. Using a
    dedicated exception type makes it easier to distinguish configuration
    problems from other runtime errors.
    """


PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER = "__PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER__"
PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR = "__PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR__"
PACKAGED_PLACEHOLDER_LIBRARY_ID = "__PACKAGED_PLACEHOLDER_LIBRARY_ID__"
PACKAGED_PLACEHOLDER_READ_KEY = "__PACKAGED_PLACEHOLDER_READ_KEY__"

_configs_registered = False


@dataclass
class TagRuleConfig:
    """Configuration for a single tag-matching rule."""

    values: list[str] = field(default_factory=list)
    """Tag values to match against."""

    operator: str = "or"
    """Logical operator for combining tag matches."""


@dataclass
class TagSelectionConfig:
    """Configuration for selecting items by tag inclusion/exclusion rules."""

    include: TagRuleConfig = field(
        default_factory=lambda: TagRuleConfig(values=["millefeuille"])
    )
    """Rule for tags that must be present on an item."""

    exclude: TagRuleConfig = field(default_factory=TagRuleConfig)
    """Rule for tags that must not be present on an item."""

    conflict_resolution: str = "exclude_wins"
    """How to resolve when an item matches both include and exclude rules."""


@dataclass
class TagTargetConfig:
    """Configuration for tags to apply to items after processing."""

    values: list[str] = field(default_factory=list)
    """Tag values to apply."""


@dataclass
class TaggingConfig:
    """Configuration for the tag-based item selection and post-processing workflow."""

    selection: TagSelectionConfig = field(default_factory=TagSelectionConfig)
    """Rules for selecting items to process based on tags."""

    apply_on_success: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to apply to items after successful processing."""

    apply_on_error: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to apply to items after failed processing."""

    remove_on_success: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to remove from items after successful processing."""

    remove_on_error: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to remove from items after failed processing."""

    include_abstract: bool = False
    """Whether to include the item abstract in processing."""

    def __post_init__(self) -> None:
        """Validate and normalize tagging configuration."""
        if not self.selection.include.values:
            raise ConfigError(
                "tagging.selection.include.values cannot be empty"
            )

        if self.selection.include.operator not in {"and", "or"}:
            raise ConfigError(
                f"tagging.selection.include.operator must be 'and' or 'or', "
                f"got {self.selection.include.operator!r}"
            )
        if self.selection.exclude.operator not in {"and", "or"}:
            raise ConfigError(
                f"tagging.selection.exclude.operator must be 'and' or 'or', "
                f"got {self.selection.exclude.operator!r}"
            )

        if self.selection.conflict_resolution not in {"exclude_wins", "include_wins"}:
            raise ConfigError(
                f"tagging.selection.conflict_resolution must be "
                f"'exclude_wins' or 'include_wins', "
                f"got {self.selection.conflict_resolution!r}"
            )

        _validate_tag_values(
            self.selection.include.values,
            "tagging.selection.include.values",
        )
        _validate_tag_values(
            self.selection.exclude.values,
            "tagging.selection.exclude.values",
        )
        _validate_tag_values(
            self.apply_on_success.values,
            "tagging.apply_on_success.values",
        )
        _validate_tag_values(
            self.apply_on_error.values,
            "tagging.apply_on_error.values",
        )
        _validate_tag_values(
            self.remove_on_success.values,
            "tagging.remove_on_success.values",
        )
        _validate_tag_values(
            self.remove_on_error.values,
            "tagging.remove_on_error.values",
        )

        self.selection.include.values = _strip_dedup(
            self.selection.include.values,
            "tagging.selection.include.values",
        )
        self.selection.exclude.values = _strip_dedup(
            self.selection.exclude.values,
            "tagging.selection.exclude.values",
        )
        self.apply_on_success.values = _strip_dedup(
            self.apply_on_success.values,
            "tagging.apply_on_success.values",
        )
        self.apply_on_error.values = _strip_dedup(
            self.apply_on_error.values,
            "tagging.apply_on_error.values",
        )
        self.remove_on_success.values = _strip_dedup(
            self.remove_on_success.values,
            "tagging.remove_on_success.values",
        )
        self.remove_on_error.values = _strip_dedup(
            self.remove_on_error.values,
            "tagging.remove_on_error.values",
        )

        success_overlap = set(self.apply_on_success.values) & set(
            self.remove_on_success.values
        )
        if success_overlap:
            raise ConfigError(
                "tagging.apply_on_success and tagging.remove_on_success share "
                f"conflicting tags: {sorted(success_overlap)!r}. A tag cannot be "
                "both applied and removed for the same outcome."
            )
        error_overlap = set(self.apply_on_error.values) & set(
            self.remove_on_error.values
        )
        if error_overlap:
            raise ConfigError(
                "tagging.apply_on_error and tagging.remove_on_error share "
                f"conflicting tags: {sorted(error_overlap)!r}. A tag cannot be "
                "both applied and removed for the same outcome."
            )


def _validate_tag_values(values: list[object], field_name: str) -> None:
    """Validate that tag values are strings and not empty after trimming."""
    for v in values:
        if not isinstance(v, str):
            raise ConfigError(
                f"{field_name} contains a non-string tag value: {v!r}"
            )
        if not v.strip():
            raise ConfigError(
                f"{field_name} contains a whitespace-only or empty tag: {v!r}"
            )


def _strip_dedup(values: list[object], field_name: str) -> list[str]:
    """Strip whitespace and deduplicate a list of strings, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        if not isinstance(v, str):
            raise ConfigError(
                f"{field_name} contains a non-string tag value: {v!r}"
            )
        stripped = v.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result


@dataclass
class AuthQueryConfig:
    """Zotero API credentials and log-redaction for authenticated queries.

    Library ID and API keys for connection to Zotero are set via environment
    variables (see :func:`millefeuille.cli.main.build_app_config`).
    """

    library_id: str
    read_key: str
    write_key: str | None = None
    redact_logs: bool = True

    def __post_init__(self) -> None:
        if (
            not self.library_id
            or not str(self.library_id).strip()
            or self.library_id == PACKAGED_PLACEHOLDER_LIBRARY_ID
        ):
            raise ConfigError(
                "ZOTERO_LIBRARY_ID must be set to your numeric user library ID "
                "(not empty, not a packaged placeholder). "
                "Find it in your Zotero web library URL: "
                "https://www.zotero.org/users/{id}"
            )
        if (
            not self.read_key
            or not str(self.read_key).strip()
            or self.read_key == PACKAGED_PLACEHOLDER_READ_KEY
        ):
            raise ConfigError(
                "ZOTERO_READ_KEY is required for Zotero read operations. "
                "Set it to a Zotero API key with read access."
            )


@dataclass
class ZoteroConfig:
    """Zotero integration settings that are not connection credentials.

    Connection and authentication are configured via :class:`AuthQueryConfig`
    and environment variables. This group holds feature flags such as error
    tagging after failed processing.
    """

    error_tagging_enabled: bool = True
    """Whether to add error tags to failed items. Makes it easy to identify
    failed items in Zotero."""


@dataclass
class OCRProviderConfig:
    """Base configuration class for OCR provider implementations.

    All OCR provider configs must inherit from this base class and implement
    provider-specific fields. The provider field is used for runtime selection.
    """

    enabled: bool = True
    """Whether OCR processing is enabled. When False, only PDF download will occur."""

    provider: str = "mistral"
    """OCR provider identifier. Options: 'mistral', 'pageindex'"""

    def __post_init__(self) -> None:
        """Validate OCR provider configuration."""
        allowed_ocr_providers = {"mistral", "pageindex"}
        # Note: api_key validation is done in subclasses
        # (MistralOCRConfig, PageIndexOCRConfig) because api_key is not
        # defined in this base class
        if self.enabled is True and self.provider not in allowed_ocr_providers:
            raise ConfigError(
                f"provider must be one of {allowed_ocr_providers}, "
                f"got '{self.provider}'"
            )


@dataclass
class MistralOCRConfig(OCRProviderConfig):
    """Configuration for Mistral AI OCR API.

    Contains settings for the Mistral AI OCR service, including model selection,
    output format preferences, and extraction options.
    """

    provider: str = "mistral"
    """OCR provider identifier. Set to 'mistral' for Mistral OCR."""

    api_key: str = ""
    """Mistral AI API key. Obtain from https://console.mistral.ai"""

    model: str = "mistral-ocr-latest"
    """OCR model to use. Default uses the latest available model."""

    table_format: str = "markdown"
    """Format for extracted tables. Markdown is recommended for readability
    in Zotero notes."""

    include_image_base64: bool = True
    """Whether to include images as base64-encoded strings in notes."""

    embed_images_in_markdown: bool = False
    """Whether to embed images inline in markdown as base64 data URIs.
    When false (default), image placeholders (e.g., ![img-0.jpeg](img-0.jpeg))
    remain in markdown and images are available separately in
    PageContent.images list. Requires include_image_base64=True to have any
    effect."""

    extract_header: bool = False
    """Whether to extract document headers. May increase processing time."""

    extract_footer: bool = False
    """Whether to extract document footers. May increase processing time."""

    def __post_init__(self) -> None:
        """Validate Mistral OCR configuration."""
        super().__post_init__()  # Call parent validation
        if self.enabled is True and (not self.api_key or not self.api_key.strip()):
            raise ConfigError("api_key is required when OCR is enabled")


@dataclass
class PageIndexOCRConfig(OCRProviderConfig):
    """Configuration for PageIndex OCR API.

    Contains settings for the PageIndex OCR service, including API authentication,
    batch processing options, and polling configuration with exponential backoff.
    """

    provider: str = "pageindex"
    """OCR provider identifier. Set to 'pageindex' for PageIndex OCR."""

    api_key: str = ""
    """PageIndex API key. Obtain from PageIndex console."""

    base_url: str = "https://api.pageindex.ai"
    """Base URL for PageIndex API. Default points to production endpoint."""

    use_sdk: bool = False
    """Whether to use PageIndex Python SDK instead of direct HTTP API requests.

    When True: Uses official PageIndex SDK (pageindex package) for all
    PageIndex operations. The SDK provides automatic API versioning, simplified
    error handling, and access to SDK-specific features.

    When False (default): Uses direct HTTP requests via the requests library.
    This is the default mode for backward compatibility with existing
    deployments.

    Both modes provide identical functionality and performance. The choice
    between SDK and HTTP API mode is a matter of preference and operational
    requirements.

    Requirements:
    - SDK mode requires the pageindex package to be installed
      (included in environment.yml)
    - Both modes require a valid PageIndex API key

    Default: false (HTTP API mode) for backward compatibility.
    """

    batch_upload_enabled: bool = True
    """Whether to enable batch upload optimization. When enabled, documents
    are uploaded in batch mode and then polled sequentially, improving
    efficiency for multiple documents."""

    polling_initial_interval: int = 2
    """Starting interval in seconds for exponential backoff polling. The
    interval doubles on each retry until reaching polling_max_interval."""

    polling_max_interval: int = 30
    """Maximum interval cap in seconds for exponential backoff polling.
    Prevents intervals from growing too large and ensures reasonable response
    times."""

    polling_timeout: int = 300
    """Overall timeout in seconds for polling operations. If polling exceeds
    this duration, the operation will be aborted. Default 300 seconds
    (5 minutes) provides sufficient time for typical document processing."""

    polling_max_attempts: int = 20
    """Maximum number of polling attempts per document. With exponential
    backoff starting at polling_initial_interval, this provides adequate
    coverage for document processing while preventing infinite retry loops."""

    polling_batch_delay: int = 1
    """Delay in seconds between processing documents in a batch. Prevents API
    rate limiting when polling multiple documents sequentially."""

    def __post_init__(self) -> None:
        """Validate PageIndex OCR configuration."""
        super().__post_init__()  # Call parent validation
        if self.enabled is True and (not self.api_key or not self.api_key.strip()):
            raise ConfigError("api_key is required when OCR is enabled")


@dataclass
class RetryConfig:
    """Configuration for retry behavior with exponential backoff."""

    max_attempts: int = 3
    """Maximum number of retry attempts before giving up."""

    initial_delay: int = 2
    """Initial delay in seconds before first retry."""

    backoff_multiplier: float = 2.0
    """Multiplier for exponential backoff. Delay doubles by default on each retry."""

    max_delay: int = 16
    """Maximum delay cap in seconds to prevent excessively long waits."""

    def __post_init__(self) -> None:
        """Validate retry configuration parameters."""
        if self.max_attempts <= 0:
            raise ConfigError("max_attempts must be greater than 0")
        if self.initial_delay <= 0:
            raise ConfigError("initial_delay must be greater than 0")
        if self.backoff_multiplier <= 0:
            raise ConfigError("backoff_multiplier must be greater than 0")
        if self.max_delay <= 0:
            raise ConfigError("max_delay must be greater than 0")
        if self.max_delay < self.initial_delay:
            raise ConfigError(
                "max_delay must be greater than or equal to initial_delay"
            )


@dataclass
class DownloadConfig:
    """Configuration for PDF download feature. Controls whether and how PDFs
    are downloaded from Zotero items."""

    enabled: bool = False
    """Whether PDF download feature is enabled. Default False for backward
    compatibility."""

    upload_folder: str = PACKAGED_PLACEHOLDER_DOWNLOAD_FOLDER
    """Local directory path where downloaded PDFs will be saved."""

    preserve_filenames: bool = True
    """Whether to preserve original PDF filenames. When False, files are
    renamed based on Zotero item metadata."""

    create_subfolders: bool = False
    """Whether to create subfolders for organizing downloads (e.g., by
    collection or date)."""

    skip_existing: bool = True
    """Whether to skip downloading PDFs that already exist locally. Prevents
    redundant downloads."""

    max_concurrent_downloads: int = 5
    """Maximum number of concurrent download operations. Must be greater than 0."""

    retry: RetryConfig = field(default_factory=RetryConfig)
    """Retry configuration for failed download attempts."""

    def __post_init__(self) -> None:
        """Validate download configuration parameters."""
        if not self.upload_folder or not self.upload_folder.strip():
            raise ConfigError(
                "upload_folder is required and cannot be empty or whitespace-only. "
                "Specify a valid directory path for downloads."
            )
        if self.max_concurrent_downloads <= 0:
            raise ConfigError(
                "max_concurrent_downloads must be greater than 0. "
                "Specify a positive integer for concurrent downloads."
            )
        if not isinstance(self.retry, RetryConfig):
            raise ConfigError("retry must be an instance of RetryConfig")


@dataclass
class TagAddingConfig:
    """Configuration for the Tag Adding feature."""

    enabled: bool = False
    """Whether the Tag Adding feature is enabled."""

    assignments: dict[str, list[str]] = field(default_factory=dict)
    """Per-item tag assignments. Maps each citation key to its own list of tags
    to apply. Citation key matching is exact and case-sensitive
    (whitespace-trimmed). Keys are resolved from Zotero items using the
    following precedence:
    1. Native Zotero 8+ field: ``item["data"]["citationKey"]`` (preferred when
       present and non-empty).
    2. Legacy Better BibTeX fallback: a ``Citation Key: <key>`` line in
       ``item["data"]["extra"]``.
    """

    replace_all_existing_tags: bool = False
    """Destructive, opt-in flag. When ``True``, ALL existing tags on matched
    items are deleted and replaced with their assigned tags in a single API
    call. Has no effect when ``enabled`` is ``False``."""

    def __post_init__(self) -> None:
        """Normalize and validate tag-adding assignments."""
        # 1. Empty-assignments guard
        if self.enabled and not self.assignments:
            raise ConfigError(
                "tag_adding.assignments cannot be empty when tag_adding is "
                "enabled. Provide at least one citation_key → tags mapping."
            )

        if not isinstance(self.assignments, Mapping):
            raise ConfigError(
                "tag_adding.assignments must be a mapping of citation_key → "
                f"list[str], got {type(self.assignments).__name__!r}"
            )

        # 2. Key collision check (includes empty-key guard)
        seen_keys: dict[str, str] = {}
        for raw_key in self.assignments:
            if not isinstance(raw_key, str):
                raise ConfigError(
                    "tag_adding.assignments keys must be strings, got "
                    f"{type(raw_key).__name__!r} (value: {raw_key!r})"
                )
            normalized_key = raw_key.strip()
            if not normalized_key:
                raise ConfigError(
                    f"tag_adding.assignments contains an invalid key "
                    f"{raw_key!r} that is empty or whitespace-only after "
                    f"normalization. All citation keys must be non-empty "
                    f"strings."
                )
            if normalized_key in seen_keys:
                raise ConfigError(
                    f"tag_adding.assignments has duplicate citation keys after "
                    f"stripping whitespace: {seen_keys[normalized_key]!r} and "
                    f"{raw_key!r} both normalize to {normalized_key!r}. "
                    f"Use unique keys."
                )
            seen_keys[normalized_key] = raw_key

        # 3. Value type check
        for citation_key, value in self.assignments.items():
            if not isinstance(value, list):
                raise ConfigError(
                    f"tag_adding.assignments[{citation_key!r}] must be a list "
                    f"of strings, got {type(value).__name__!r}"
                )

        # 4. Element type check
        for citation_key, value in self.assignments.items():
            for i, t in enumerate(value):
                if not isinstance(t, str):
                    raise ConfigError(
                        f"tag_adding.assignments[{citation_key!r}] must "
                        f"contain strings only, but entry at index {i} has "
                        f"type {type(t).__name__!r} (value: {t!r})"
                    )

        # 5. Per-item normalization + rebuild
        rebuilt: dict[str, list[str]] = {}
        for citation_key, value in self.assignments.items():
            if not isinstance(citation_key, str):
                raise ConfigError(
                    "tag_adding.assignments keys must be strings, got "
                    f"{type(citation_key).__name__!r} (value: {citation_key!r})"
                )
            seen: set[str] = set()
            normalized: list[str] = []
            for t in value:
                stripped = t.strip()
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    normalized.append(stripped)
            rebuilt[citation_key.strip()] = normalized
        self.assignments = rebuilt

        # 6. Empty-tag-list guard
        for citation_key, tags in self.assignments.items():
            if not tags:
                raise ConfigError(
                    f"tag_adding.assignments[{citation_key!r}] has no valid "
                    f"tags after normalization (all entries were empty or "
                    f"whitespace-only). Provide at least one non-empty tag."
                )


@dataclass
class SelectionTaggingConfig:
    """Configuration for bulk tag add/remove on selected items."""

    enabled: bool = False
    """Whether the Selection Tagging feature is enabled."""

    add: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to add to every selected item."""

    remove: TagTargetConfig = field(default_factory=TagTargetConfig)
    """Tags to remove from every selected item."""

    def __post_init__(self) -> None:
        """Validate and normalize selection tagging configuration."""
        _validate_tag_values(
            self.add.values,
            "selection_tagging.add.values",
        )
        _validate_tag_values(
            self.remove.values,
            "selection_tagging.remove.values",
        )
        self.add.values = _strip_dedup(
            self.add.values,
            "selection_tagging.add.values",
        )
        self.remove.values = _strip_dedup(
            self.remove.values,
            "selection_tagging.remove.values",
        )
        overlap = set(self.add.values) & set(self.remove.values)
        if overlap:
            raise ConfigError(
                "selection_tagging.add and selection_tagging.remove share "
                f"conflicting tags: {sorted(overlap)!r}. A tag cannot be both "
                "added and removed."
            )


@dataclass
class AuthQueryHelperConfig:
    """Feature helper flags for the authenticated URL export / auth query path."""

    enabled: bool = False


@dataclass
class AttachmentUrlExportConfig:
    """Configuration for exporting discovered attachment URLs."""

    enabled: bool = False
    """Whether attachment URL export is enabled."""

    log: bool = True
    """Whether to log export activity."""

    write_manifest: bool = False
    """Whether to write a manifest file to disk."""

    manifest_path: str | None = None
    """Path for the manifest file when write_manifest is True."""

    format: str = "json"
    """Export format identifier."""

    auth_query: AuthQueryHelperConfig = field(default_factory=AuthQueryHelperConfig)

    def __post_init__(self) -> None:
        """Validate attachment URL export configuration."""
        if self.write_manifest and (
            self.manifest_path is None or self.manifest_path.strip() == ""
        ):
            raise ConfigError(
                "export.attachment_urls.manifest_path must be set when "
                "write_manifest=true. Override with: "
                "export.attachment_urls.manifest_path=./path/to/file.json"
            )
        if self.format not in {"json"}:
            raise ConfigError(
                f"export.attachment_urls.format must be 'json', got {self.format!r}"
            )


@dataclass
class OpenKBHandoffExportConfig:
    """Configuration for OpenKB/Millefeuille JSONL handoff export."""

    enabled: bool = False
    """Whether OpenKB handoff export is enabled."""

    jsonl_path: str | None = None
    """Output path for the handoff JSONL file."""

    preview_jsonl_path: str | None = None
    """Optional preview JSONL path for dry-run or validate-only runs."""

    include_item_type: bool = True
    """Whether to include parent item type in handoff rows."""

    include_zotero_version: bool = True
    """Whether to include Zotero item version in handoff rows."""

    compute_sha256: bool = False
    """Whether to transiently fetch PDF bytes and compute SHA-256 hashes."""


@dataclass
class ArtifactExportConfig:
    """Configuration for writing Millefeuille artifact indexes from dry-runs."""

    enabled: bool = False
    """Whether to write artifact-index and stage-manifest files."""

    artifact_root: str | None = None
    """Explicit artifact root directory or ``source-pack`` root mode."""

    source_pack_root: str | None = None
    """Base directory used when ``artifact_root`` is ``source-pack``."""

    source_pack_intake_evidence_path: str | None = None
    """Optional recovered-PDF evidence file for staged source-pack intake."""

    native_extraction_evidence_path: str | None = None
    """Optional native extraction fixture evidence file for source packs."""

    ocr_extraction_evidence_path: str | None = None
    """Optional OCR extraction fixture evidence file for source packs."""

    route_selection_evidence_path: str | None = None
    """Optional route-selection fixture evidence file for source packs."""

    structure_evidence_path: str | None = None
    """Optional structure fixture evidence file for source packs."""

    run_id: str | None = None
    """Optional stable run id. When omitted, a timestamped run id is generated."""

    def __post_init__(self) -> None:
        if self.enabled and (
            self.artifact_root is None or not str(self.artifact_root).strip()
        ):
            raise ConfigError(
                "export.artifacts.artifact_root must be set when "
                "export.artifacts.enabled=true. Use --artifact-root /path or "
                "export.artifacts.artifact_root=/path|source-pack."
            )
        if self.source_pack_root is not None and not str(self.source_pack_root).strip():
            raise ConfigError("export.artifacts.source_pack_root cannot be empty")
        if self.source_pack_intake_evidence_path is not None and not str(
            self.source_pack_intake_evidence_path
        ).strip():
            raise ConfigError(
                "export.artifacts.source_pack_intake_evidence_path cannot be empty"
            )
        if self.native_extraction_evidence_path is not None and not str(
            self.native_extraction_evidence_path
        ).strip():
            raise ConfigError(
                "export.artifacts.native_extraction_evidence_path cannot be empty"
            )
        if self.ocr_extraction_evidence_path is not None and not str(
            self.ocr_extraction_evidence_path
        ).strip():
            raise ConfigError(
                "export.artifacts.ocr_extraction_evidence_path cannot be empty"
            )
        if self.route_selection_evidence_path is not None and not str(
            self.route_selection_evidence_path
        ).strip():
            raise ConfigError(
                "export.artifacts.route_selection_evidence_path cannot be empty"
            )
        if self.structure_evidence_path is not None and not str(
            self.structure_evidence_path
        ).strip():
            raise ConfigError(
                "export.artifacts.structure_evidence_path cannot be empty"
            )
        if self.run_id is not None and not str(self.run_id).strip():
            raise ConfigError("export.artifacts.run_id cannot be empty")


@dataclass
class ExportConfig:
    """Top-level export feature configuration."""

    attachment_urls: AttachmentUrlExportConfig = field(
        default_factory=AttachmentUrlExportConfig
    )
    """Attachment URL export settings."""

    openkb_handoff: OpenKBHandoffExportConfig = field(
        default_factory=OpenKBHandoffExportConfig
    )
    """OpenKB/Millefeuille JSONL handoff export settings."""

    artifacts: ArtifactExportConfig = field(default_factory=ArtifactExportConfig)
    """Artifact-index and stage-manifest export settings."""


@dataclass
class TreeStructureConfig:
    """Configuration for document tree structure extraction and processing.

    Controls whether and how document tree structures (such as table of contents,
    section hierarchies, or nested document structures) are extracted during OCR
    processing. Tree structures provide hierarchical organization of document content,
    enabling navigation and structured content extraction.

    When enabled, tree structures are extracted during OCR processing and saved to
    disk as JSON files in the format {item_dir}/{pdf_name}_tree_structure.json.
    The tree structure represents the document's organizational hierarchy, with nodes
    containing titles, page references, optional summaries, and optional text content.

    Tree structure extraction occurs automatically during OCR processing when enabled.
    The extraction is provider-specific (currently only PageIndex supports tree
    structure extraction). Performance may be impacted when including full text content
    in tree nodes, as this significantly increases the size of the tree structure.
    """

    enabled: bool = False
    """Whether tree structure extraction is enabled. When True, tree structures are
    extracted during OCR processing and saved to disk. When False (default), tree
    structure extraction is skipped entirely."""

    provider: str = "pageindex"
    """Tree structure provider identifier. Currently only 'pageindex' is supported
    for tree structure extraction. Other OCR providers may support tree extraction
    in future versions."""

    node_id: bool = True
    """Whether to include node IDs in tree structure. Node IDs provide unique
    identifiers for each node, enabling programmatic navigation and referencing
    within the tree structure."""

    summary: bool = True
    """Whether to include node summaries in tree structure. Summaries provide
    condensed overviews of node content without including full text, balancing
    information content with storage efficiency."""

    text: bool = False
    """Whether to include full text content in tree nodes. When True, each node
    includes the complete text content for its section, significantly increasing
    tree structure size. When False (default), only metadata (title, page index,
    summary) is included, reducing storage requirements and improving performance.
    Set to True only when full text content is required for downstream processing."""

    description: bool = True
    """Whether to include document description in tree structure. Document descriptions
    provide additional metadata about the document's content, purpose, or structure,
    enhancing the tree structure's informational value."""


@dataclass
class ProcessingConfig:
    """Configuration for document processing behavior.

    Controls how the pipeline processes documents, including batch sizes,
    error handling, and optional features.
    """

    dry_run: bool = False
    """Preview mode without actual processing. Useful for testing
    configuration."""

    batch_size: int = 50
    """Number of notes to process in each batch. Must not exceed 50 (Zotero
    API limit)."""

    skip_empty_pages: bool = True
    """Whether to skip pages with no content. Reduces clutter in Zotero library."""

    save_to_disk: bool = False
    """Whether to save processed content to disk. Useful for debugging."""

    note_size_threshold: int = 180000
    """Maximum note content size in bytes (character count) before triggering
    size validation. Default 180000 bytes provides safety margin below
    Zotero's 250KB limit to account for HTML encoding overhead. Notes
    exceeding this threshold will either be split automatically (if
    auto_split_oversized_notes is true) or raise NoteSizeExceededError.
    Recommendation: Keep default unless experiencing size-related API
    failures."""

    auto_split_oversized_notes: bool = True
    """Whether to automatically split oversized notes into smaller chunks.
    When enabled, page notes exceeding note_size_threshold are split into
    multiple sequential notes (e.g., 'Page 1 - Part 1', 'Page 1 - Part 2').
    Figure and table notes cannot be split and will raise
    NoteSizeExceededError if oversized. Set to false to fail fast on
    oversized content for manual review."""

    extraction_mode: str = "all_at_once"
    """How to organize extracted content into notes.
Options:
- 'all_at_once': Create a single note containing all pages (default)
- 'page_by_page': Create one note per page (legacy behavior, opt-in)

When 'all_at_once' is selected (default):
- All pages are concatenated into one note
- Page separators (e.g., '--- Page N ---') are inserted between pages
- Size limits still apply (auto-split if content exceeds threshold)
- Empty pages are still filtered if skip_empty_pages=True

When 'page_by_page' is selected (legacy):
- One note is created per page
- Each note has its own title with page number
- Useful for users who prefer page-by-page organization
"""

    cleanup_uploaded_files: bool = False
    """Controls whether uploaded OCR files are deleted after processing.

    When True: Files are cleaned up from OCR provider storage after
    processing completes. This helps avoid storage costs in production
    environments. When False: Files remain in OCR provider storage.

    When False (default): Files are retained on OCR provider storage for
    debugging/review. This is useful during development and troubleshooting to
    inspect uploaded files.

    Recommendation: Set to True in production to avoid storage costs, False
    for debugging.
    """

    def __post_init__(self) -> None:
        """Validate processing configuration parameters."""
        allowed_extraction_modes = {"all_at_once", "page_by_page"}
        if self.extraction_mode not in allowed_extraction_modes:
            raise ConfigError(
                f"extraction_mode must be one of {allowed_extraction_modes}, "
                f"got '{self.extraction_mode}'"
            )


@dataclass
class StorageConfig:
    """Configuration for optional disk storage features.

    These features are primarily useful for debugging and development.
    All storage is optional and can be disabled for production use.
    """

    base_dir: str = PACKAGED_PLACEHOLDER_STORAGE_BASE_DIR
    """Root directory for optional disk storage. Created automatically if
    it doesn't exist."""

    save_pdfs: bool = False
    """Whether to save downloaded PDFs to disk. Can use significant disk space."""

    save_manifests: bool = True
    """Whether to save processing metadata as JSON. Useful for debugging and
    tracking."""


@dataclass
class AppConfig:
    """Top-level application configuration.

    Combines all configuration groups into a single type-safe configuration
    object. This is the configuration class that Hydra will instantiate and
    pass to the main function.
    """

    zotero: ZoteroConfig
    """Zotero feature flags (e.g. error tagging), not connection credentials."""

    ocr: OCRProviderConfig
    """OCR provider configuration (Mistral, PageIndex, etc.)."""

    processing: ProcessingConfig
    """Processing behavior configuration."""

    storage: StorageConfig
    """Storage options configuration."""

    credentials: AuthQueryConfig
    """Zotero connection credentials and log-redaction (from environment)."""

    tree_structure: TreeStructureConfig = field(default_factory=TreeStructureConfig)
    """Tree structure extraction and processing configuration."""

    download: DownloadConfig = field(default_factory=DownloadConfig)
    """PDF download feature configuration."""

    tag_adding: TagAddingConfig = field(default_factory=TagAddingConfig)
    """Tag Adding feature configuration."""

    export: ExportConfig = field(default_factory=ExportConfig)
    """Export feature configuration."""

    tagging: TaggingConfig = field(default_factory=TaggingConfig)
    """Tag-based item selection and post-processing workflow configuration."""

    selection_tagging: SelectionTaggingConfig = field(
        default_factory=SelectionTaggingConfig
    )
    """Selection Tagging feature configuration."""


def register_configs() -> None:
    """Register structured configs with Hydra.

    This function must be called before Hydra initializes to enable type-safe
    configuration validation and IDE autocomplete support.
    """
    global _configs_registered
    if _configs_registered:
        return

    cs = ConfigStore.instance()

    # Register config groups with names distinct from YAML files to avoid
    # deprecated automatic schema matching. Each YAML file explicitly extends
    # its schema via its defaults list.
    cs.store(group="credentials", name="default", node=AuthQueryConfig)
    cs.store(group="zotero", name="base_default", node=ZoteroConfig)

    # Register OCR provider configs
    cs.store(group="ocr", name="base_mistral", node=MistralOCRConfig)
    cs.store(group="ocr", name="base_pageindex", node=PageIndexOCRConfig)
    # Note: OCRProviderConfig is abstract base class, not registered as default
    # Users must select a concrete provider (mistral or pageindex)

    cs.store(group="processing", name="base_default", node=ProcessingConfig)
    cs.store(group="storage", name="base_default", node=StorageConfig)
    cs.store(group="tree_structure", name="base_default", node=TreeStructureConfig)
    cs.store(group="download", name="base_default", node=DownloadConfig)
    cs.store(group="tag_adding", name="base_default", node=TagAddingConfig)
    cs.store(group="export", name="base_default", node=ExportConfig)
    cs.store(group="tagging", name="base_default", node=TaggingConfig)
    cs.store(
        group="selection_tagging", name="base_default", node=SelectionTaggingConfig
    )

    # Register top-level config
    cs.store(name="base_config", node=AppConfig)
    _configs_registered = True
