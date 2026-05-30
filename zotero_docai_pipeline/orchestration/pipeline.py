"""
Pipeline orchestrates the end-to-end processing workflow for Zotero Document AI.

This module provides the Pipeline class which coordinates the complete processing
workflow: discovering items using tag-based filtering, processing each item through
ItemProcessor, handling per-item errors with appropriate tagging, and aggregating
results for summary reporting. The pipeline implements graceful error handling where
individual item failures don't stop the entire pipeline.

The pipeline follows a tag-based workflow:
- Items with the input tag (e.g., "docai") are discovered for processing
- Items with the output tag (e.g., "docai-processed") are excluded from discovery
- Successfully processed items receive the output tag
- Failed items optionally receive an error tag (e.g., "docai-error") if enabled

Example usage:
    >>> from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
    >>> from zotero_docai_pipeline.clients.ocr_client import OCRClient
    >>> from zotero_docai_pipeline.orchestration.processor import ItemProcessor
    >>> from zotero_docai_pipeline.orchestration.pipeline import Pipeline
    >>> from zotero_docai_pipeline.domain.config import (
    ...     ZoteroConfig, ProcessingConfig, StorageConfig
    ... )
    >>>
    >>> processor = ItemProcessor(zotero_client, ocr_client, processing_config)
    >>> pipeline = Pipeline(
    ...     zotero_client=zotero_client,
    ...     ocr_client=ocr_client,
    ...     processor=processor,
    ...     zotero_config=zotero_config,
    ...     processing_config=processing_config,
    ...     storage_config=storage_config
    ... )
    >>> summary = pipeline.run()
    >>> print(f"Processed {summary['total_items']} items, "
    ...       f"{summary['successful_items']} successful, "
    ...       f"{summary['failed_items']} failed")
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
import time
from typing import Any

try:
    from hydra.utils import get_original_cwd
except ImportError:
    # Hydra not available (e.g., in tests), fall back to current directory
    def get_original_cwd() -> str:
        return os.getcwd()


from zotero_docai_pipeline.clients.exceptions import (
    TreeStructureProcessingError,
    ZoteroClientError,
)
from zotero_docai_pipeline.clients.ocr_client import OCRClient, OCRProvider
from zotero_docai_pipeline.clients.zotero_client import ZoteroClient
from zotero_docai_pipeline.domain.config import (
    ConfigError,
    DownloadConfig,
    ExportConfig,
    OCRProviderConfig,
    ProcessingConfig,
    StorageConfig,
    TagAddingConfig,
    TaggingConfig,
    TreeStructureConfig,
    ZoteroConfig,
)
from zotero_docai_pipeline.domain.models import (
    AttachmentInfo,
    DiscoveredItem,
    DiscoveryStats,
    DocumentTree,
    PageContent,
    ProcessingResult,
    TagAddingResult,
    UploadedDocument,
)
from zotero_docai_pipeline.domain.tree_processor import TreeStructureProcessor
from zotero_docai_pipeline.orchestration.processor import ItemProcessor
from zotero_docai_pipeline.utils.export import (
    build_export_records,
    log_export_records,
    write_manifest,
)
from zotero_docai_pipeline.utils.logging import (
    log_completion,
    log_disk_save,
    log_error,
    log_startup,
    log_tag_adding_result,
    log_tag_adding_start,
)
from zotero_docai_pipeline.utils.progress import ProgressBar
from zotero_docai_pipeline.utils.redaction import redact_url
from zotero_docai_pipeline.utils.retry import retry_with_backoff


@dataclass
class OutcomeTagPlan:
    """Resolved add/remove tag lists for a processing outcome."""

    outcome: str
    tags_to_add: list[str]
    tags_to_remove: list[str]


@dataclass
class ProcessingTagResult:
    """Counts from applying outcome tags to a single item."""

    outcome: str
    add_attempted: int
    add_succeeded: int
    add_failed: int
    remove_attempted: int
    remove_succeeded: int
    remove_failed: int

    @property
    def has_failures(self) -> bool:
        return self.add_failed > 0 or self.remove_failed > 0

    @property
    def had_planned_work(self) -> bool:
        return self.add_attempted > 0 or self.remove_attempted > 0

    @property
    def all_operations_succeeded(self) -> bool:
        """True when every planned tag operation succeeded."""
        if not self.had_planned_work:
            return False
        return not self.has_failures

    @property
    def had_partial_success(self) -> bool:
        """True when some operations succeeded and others failed."""
        if not self.had_planned_work or not self.has_failures:
            return False
        return self.add_succeeded > 0 or self.remove_succeeded > 0

    @property
    def all_operations_failed(self) -> bool:
        """True when operations were planned but none succeeded."""
        if not self.had_planned_work:
            return False
        return self.add_succeeded == 0 and self.remove_succeeded == 0


def _plan_outcome_tags(
    tagging_config: TaggingConfig,
    zotero_config: ZoteroConfig,
    outcome: str,
) -> OutcomeTagPlan:
    """Resolve tags to add and remove for a success or failure outcome."""
    if outcome == "success":
        tags_to_add = tagging_config.apply_on_success.values
        tags_to_remove = tagging_config.remove_on_success.values
    elif outcome == "failure":
        tags_to_add = (
            tagging_config.apply_on_error.values
            if zotero_config.error_tagging_enabled
            else []
        )
        tags_to_remove = tagging_config.remove_on_error.values
    else:
        raise ValueError(f"Invalid outcome: {outcome!r}")

    return OutcomeTagPlan(
        outcome=outcome,
        tags_to_add=tags_to_add,
        tags_to_remove=tags_to_remove,
    )


class Pipeline:
    """Orchestrates the end-to-end processing workflow for Zotero Document AI.

    This class coordinates the complete processing workflow by:
    - Discovering items using tag-based filtering (input tag, excluding output tag)
    - Processing each item through ItemProcessor
    - Handling per-item errors with appropriate tagging
    - Aggregating results for summary reporting

    The pipeline implements graceful error handling where individual item failures
    don't stop the entire pipeline. Each item is processed independently, and
    errors are logged and tracked without affecting other items.

    Attributes:
        zotero_client: Client for interacting with Zotero API (item discovery,
        tag management).
        ocr_client: Client for interacting with OCR API (passed to ItemProcessor).
        processor: ItemProcessor instance for per-item processing. Receives the
            TreeStructureProcessor instance from the pipeline when available.
        zotero_config: Configuration containing tag names and
            error_tagging_enabled flag.
        processing_config: Configuration containing save_to_disk flag.
        storage_config: Configuration containing base_dir for disk storage.
        tagging_config: Configuration for tag-based item selection and
            post-processing tag application (apply_on_success / apply_on_error).
        logger: Logger instance for this pipeline.

    Example:
        >>> processor = ItemProcessor(zotero_client, ocr_client, processing_config)
        >>> pipeline = Pipeline(
        ...     zotero_client=zotero_client,
        ...     ocr_client=ocr_client,
        ...     processor=processor,
        ...     zotero_config=zotero_config,
        ...     processing_config=processing_config,
        ...     storage_config=storage_config
        ... )
        >>> summary = pipeline.run()
        >>> if summary['successful_items'] > 0:
        ...     print(f"Successfully processed {summary['successful_items']} items")
    """

    def __init__(
        self,
        zotero_client: ZoteroClient,
        ocr_client: OCRClient | None,
        processor: ItemProcessor,
        zotero_config: ZoteroConfig,
        processing_config: ProcessingConfig,
        storage_config: StorageConfig,
        tree_structure_config: TreeStructureConfig,
        ocr_config: OCRProviderConfig,
        download_config: DownloadConfig,
        tag_adding_config: TagAddingConfig,
        tagging_config: TaggingConfig,
        tree_processor: TreeStructureProcessor | None = None,
        export_config: ExportConfig | None = None,
    ) -> None:
        """Initialize the Pipeline with dependencies.

        Args:
            zotero_client: Client for interacting with Zotero API.
            ocr_client: OCR client when OCR is enabled; None when OCR is disabled.
            processor: ItemProcessor instance for per-item processing.
            zotero_config: Configuration containing tag names and
            error_tagging_enabled flag.
            processing_config: Configuration containing save_to_disk flag.
            storage_config: Configuration containing base_dir for disk storage.
            tree_structure_config: Configuration for tree structure extraction.
            ocr_config: OCR provider configuration (used for tree client
            initialization).
            download_config: Configuration for PDF download feature.
            tag_adding_config: Configuration for tag adding feature.
            tagging_config: Configuration for tag-based item selection and
                post-processing tag application.
            export_config: Configuration for optional export features (e.g.
                attachment URL manifest). Defaults to a fresh ``ExportConfig()``
                when ``None``.
        """
        self.zotero_client = zotero_client
        self.ocr_client = ocr_client
        self.processor = processor
        self.zotero_config = zotero_config
        self.processing_config = processing_config
        self.storage_config = storage_config
        self.tree_structure_config = tree_structure_config
        self.ocr_config = ocr_config
        self.download_config = download_config
        self.tag_adding_config = tag_adding_config
        self.tagging_config = tagging_config
        self.export_config = (
            export_config if export_config is not None else ExportConfig()
        )
        self.logger = logging.getLogger(__name__)
        self._tree_structures: dict[str, DocumentTree] = {}
        self._download_path_mapping: dict[str, str] = {}
        self._replace_mode_logged: bool = False

        # Use injected TreeStructureProcessor instance (if any)
        self.tree_processor: TreeStructureProcessor | None = tree_processor

        # Pass tree processor to ItemProcessor if available
        if self.tree_processor:
            self.processor.tree_processor = self.tree_processor

        # Validate upload folder if download is enabled
        if self.download_config.enabled:
            self._ensure_upload_folder()

    def _discover_items(self) -> tuple[list[DiscoveredItem], DiscoveryStats]:
        """Discover items to process using tag-based selection rules.

        Delegates to ``ZoteroClient.get_items_by_selection()`` which applies
        the include/exclude tag rules from ``self.tagging_config.selection``.

        Returns:
            Tuple containing:
            - List of DiscoveredItem objects that matched the selection rules.
            - DiscoveryStats with matched/excluded counts and per-rule breakdown.

        Raises:
            ZoteroClientError: If item discovery fails (re-raised after logging).
        """
        try:
            items, discovery_stats = self.zotero_client.get_items_by_selection(
                self.tagging_config.selection,
                self.tagging_config.include_abstract,
            )
            return items, discovery_stats
        except ZoteroClientError as e:
            context = {
                "item_key": "N/A",
                "item_title": "N/A",
                "step": "Item Discovery",
            }
            log_error(self.logger, e, context)
            raise

    def _ensure_upload_folder(self) -> None:
        """Ensure upload folder exists and is writable.

        Creates the upload folder directory structure and tests writability
        by creating a temporary file. Raises ConfigError if folder creation
        or write test fails.

        Raises:
            ConfigError: If folder creation or write test fails.
        """
        upload_folder = Path(self.download_config.upload_folder)

        try:
            # Create directory structure
            upload_folder.mkdir(parents=True, exist_ok=True)

            # Test writability by creating a temporary file
            with tempfile.NamedTemporaryFile(dir=upload_folder, delete=True):
                pass

            self.logger.info(f"Upload folder validated: {upload_folder}")
        except OSError as e:
            raise ConfigError(
                f"Failed to create or write to upload folder '{upload_folder}': {e}"
            ) from e

    def _apply_processing_tags(
        self, item_key: str, success: bool
    ) -> ProcessingTagResult:
        """Apply post-processing tags to an item based on success/failure.

        Resolves add/remove tag lists via ``_plan_outcome_tags()``, applies
        additions first, then removals. Each operation is independently
        error-isolated so a single API failure does not prevent remaining
        tags from being updated.

        Args:
            item_key: Zotero item key to tag.
            success: Whether the item was processed successfully.

        Returns:
            ProcessingTagResult with per-operation counts.
        """
        outcome = "success" if success else "failure"
        plan = _plan_outcome_tags(
            self.tagging_config, self.zotero_config, outcome
        )
        result = ProcessingTagResult(
            outcome=outcome,
            add_attempted=0,
            add_succeeded=0,
            add_failed=0,
            remove_attempted=0,
            remove_succeeded=0,
            remove_failed=0,
        )

        for tag in plan.tags_to_add:
            result.add_attempted += 1
            try:
                self.zotero_client.add_tag(item_key, tag)
                result.add_succeeded += 1
                self.logger.debug(f"Applied tag '{tag}' to item {item_key}")
            except ZoteroClientError as e:
                result.add_failed += 1
                self.logger.warning(
                    f"Failed to apply tag '{tag}' to item {item_key}: {e}"
                )

        for tag in plan.tags_to_remove:
            result.remove_attempted += 1
            try:
                self.zotero_client.remove_tag(item_key, tag)
                result.remove_succeeded += 1
                self.logger.debug(f"Removed tag '{tag}' from item {item_key}")
            except ZoteroClientError as e:
                result.remove_failed += 1
                self.logger.warning(
                    f"Failed to remove tag '{tag}' from item {item_key}: {e}"
                )

        return result

    @staticmethod
    def _observe_processing_tag_result(
        tag_result: ProcessingTagResult,
        *,
        succeeded: int,
        skipped: int,
        partial_failed: int,
        fully_failed: int,
    ) -> tuple[int, int, int, int]:
        """Update outcome-tag summary counters from a ProcessingTagResult."""
        if tag_result.all_operations_succeeded:
            succeeded += 1
        elif not tag_result.had_planned_work:
            skipped += 1
        elif tag_result.had_partial_success:
            partial_failed += 1
        else:
            fully_failed += 1
        return succeeded, skipped, partial_failed, fully_failed

    @staticmethod
    def _format_processing_tag_summary(
        label: str,
        *,
        succeeded: int,
        skipped: int,
        partial_failed: int,
        fully_failed: int,
        add_succeeded: int = 0,
        add_failed: int = 0,
        remove_succeeded: int = 0,
        remove_failed: int = 0,
    ) -> str:
        """Build a log line describing outcome-tag post-processing results."""
        parts: list[str] = []
        if succeeded:
            parts.append(f"{succeeded} items {label}")
        if skipped:
            parts.append(
                f"{skipped} items skipped for {label} (no tag operations planned)"
            )
        if partial_failed:
            parts.append(
                f"{partial_failed} items with partial {label} failures"
            )
        if fully_failed:
            parts.append(f"{fully_failed} items with failed {label}")
        if not parts:
            return f"No {label} post-processing operations ran"
        detail = (
            f" (add: {add_succeeded} ok/{add_failed} failed, "
            f"remove: {remove_succeeded} ok/{remove_failed} failed)"
        )
        return "; ".join(parts) + detail

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename for filesystem compatibility.

        Replaces invalid filesystem characters with underscores and handles
        edge cases like empty strings or filenames with only whitespace/dots.

        Args:
            filename: Original filename to sanitize.

        Returns:
            Sanitized filename safe for filesystem use, or "unnamed" if result is empty.

        Edge Cases:
            - Filename with only invalid characters (e.g., ``"<>:|?*.pdf"``)
                → ``"_________.pdf"``
            - Filename with only dots and spaces (e.g., ``"... "``) → ``"unnamed"``
            - Filename containing null bytes (``\\x00``) → replaced with underscores
            - Very long filenames → no truncation (filesystem handles this)

        The regex pattern ``r'[<>:"|?*\\x00-\\x1f]'`` covers control characters
        including null bytes.
        """
        # Replace invalid filesystem characters
        sanitized = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename)

        # Replace forward slash and backslash
        sanitized = sanitized.replace("/", "_").replace("\\", "_")

        # Strip leading/trailing whitespace and dots
        sanitized = sanitized.strip(" .")

        # Return sanitized filename or default if empty
        return sanitized if sanitized else "unnamed"

    def _resolve_target_path(
        self, item_key: str, attachment_key: str, filename: str, item: DiscoveredItem
    ) -> Path:
        """Resolve target path for downloaded PDF with conflict resolution.

        Determines the target file path based on download configuration,
        handling subfolder creation, filename preservation, and conflict
        resolution (skip existing or append counter).

        Args:
            item_key: Zotero item key identifier.
            attachment_key: Zotero attachment key identifier.
            filename: Original PDF filename.
            item: Item dictionary for metadata access.

        Returns:
            Path object pointing to the resolved target file location.
        """
        # Get base folder - resolve relative paths relative to original working
        # directory (project root) rather than current working directory (which
        # Hydra may have changed)
        upload_folder = self.download_config.upload_folder
        base_folder = Path(upload_folder)

        # If path is relative, resolve it relative to the original working directory
        # This ensures relative paths like "../MD-ViSCo/" work as expected
        # from project root
        if not base_folder.is_absolute():
            try:
                original_cwd = get_original_cwd()
                base_folder = (Path(original_cwd) / base_folder).resolve()
            except Exception:
                # Fallback: resolve relative to current directory if Hydra unavailable
                base_folder = base_folder.resolve()

        # Create subfolder if enabled
        if self.download_config.create_subfolders:
            target_folder = base_folder / self._sanitize_filename(item_key)
        else:
            target_folder = base_folder

        # Determine target filename
        if self.download_config.preserve_filenames:
            target_filename = self._sanitize_filename(filename)
        else:
            target_filename = f"{item_key}_{attachment_key}.pdf"

        # Construct initial target path
        target_path = target_folder / target_filename

        # Handle skip_existing
        if self.download_config.skip_existing and target_path.exists():
            return target_path

        # Conflict resolution: append counter if file exists
        if target_path.exists():
            # Extract base name and extension
            stem = target_path.stem
            suffix = target_path.suffix

            counter = 1
            while target_path.exists():
                # Append counter: document.pdf -> document_1.pdf
                new_filename = f"{stem}_{counter}{suffix}"
                target_path = target_folder / new_filename
                counter += 1

        return target_path

    def _save_pdfs_to_disk(
        self, items: list[DiscoveredItem], pdfs: list[tuple[bytes, str, str, str]]
    ) -> tuple[dict[str, int], dict[str, str]]:
        """Save PDFs to disk with progress tracking and error isolation.

        Saves all collected PDFs to disk using resolved target paths.
        Implements per-PDF error isolation where individual failures don't
        stop the batch. Returns summary statistics and path mapping.

        Args:
            items: List of item dictionaries from _discover_items().
            pdfs: List of 4-tuples (pdf_bytes, filename, item_key, attachment_key).

        Returns:
            Tuple containing:
            - Dictionary with keys: 'downloaded', 'skipped', 'failed' and counts
            - Dictionary mapping f"{item_key}_{attachment_key}" to file path strings

        Note:
            ``max_concurrent_downloads=1`` enables sequential mode (one PDF at a time).
            The current implementation processes PDFs sequentially regardless of the
            config value (no actual concurrency yet). The parameter is reserved for
            future concurrent implementation.
        """
        downloaded_count = 0
        skipped_count = 0
        failed_count = 0
        path_mapping: dict[str, str] = {}

        total_pdfs = len(pdfs)

        with ProgressBar(
            total=total_pdfs, desc="Saving PDFs to disk", unit="pdf"
        ) as pbar:
            for pdf_bytes, filename, item_key, attachment_key in pdfs:
                # Find corresponding item
                item = None
                for it in items:
                    if it.key == item_key:
                        item = it
                        break

                if item is None:
                    self.logger.warning(
                        f"Item {item_key} not found in items list, skipping PDF"
                    )
                    failed_count += 1
                    pbar.update(1)
                    continue

                try:
                    # Resolve target path
                    target_path = self._resolve_target_path(
                        item_key, attachment_key, filename, item
                    )

                    # Check if file exists and skip_existing is enabled
                    if self.download_config.skip_existing and target_path.exists():
                        skipped_count += 1
                        mapping_key = f"{item_key}:::{attachment_key}"
                        path_mapping[mapping_key] = str(target_path)
                        pbar.set_postfix(
                            {
                                "Downloaded": downloaded_count,
                                "Skipped": skipped_count,
                                "Failed": failed_count,
                            }
                        )
                        pbar.update(1)
                        continue

                    # Write PDF bytes with retry
                    @retry_with_backoff(
                        max_attempts=self.download_config.retry.max_attempts,
                        initial_delay=self.download_config.retry.initial_delay,
                        backoff_multiplier=self.download_config.retry.backoff_multiplier,
                        max_delay=self.download_config.retry.max_delay,
                        exceptions=(OSError, IOError),
                    )
                    def _write_pdf_bytes(path: Path, content: bytes) -> None:
                        """Write PDF bytes to file with retry logic."""
                        path.write_bytes(content)

                    # Ensure parent directory exists
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # Write PDF
                    _write_pdf_bytes(target_path, pdf_bytes)

                    # Success: increment counter and add to mapping
                    downloaded_count += 1
                    mapping_key = f"{item_key}:::{attachment_key}"
                    path_mapping[mapping_key] = str(target_path)

                    self.logger.debug(
                        f"Saved PDF {filename} to {target_path} "
                        f"(item_key={item_key}, attachment_key={attachment_key})"
                    )

                except Exception as e:
                    failed_count += 1
                    context = {
                        "item_key": item_key,
                        "attachment_key": attachment_key,
                        "filename": filename,
                        "step": "PDF Save",
                    }
                    log_error(self.logger, e, context)

                # Update progress bar postfix
                pbar.set_postfix(
                    {
                        "Downloaded": downloaded_count,
                        "Skipped": skipped_count,
                        "Failed": failed_count,
                    }
                )
                pbar.update(1)

        # Log summary
        self.logger.info(
            f"Saved {downloaded_count}/{total_pdfs} PDFs to disk "
            f"(Skipped: {skipped_count}, Failed: {failed_count})"
        )

        download_summary = {
            "downloaded": downloaded_count,
            "skipped": skipped_count,
            "failed": failed_count,
        }

        return download_summary, path_mapping

    def _read_pdfs_from_disk(
        self, path_mapping: dict[str, str]
    ) -> list[tuple[bytes, str, str, str]]:
        """Read PDFs from disk using path mapping.

        Reads PDF files from disk using the path mapping created during
        download phase. Returns list of 4-tuples compatible with batch
        upload format.

        Args:
            path_mapping: Dictionary mapping f"{item_key}_{attachment_key}" to
            file path strings.

        Returns:
            List of 4-tuples (pdf_bytes, filename, item_key, attachment_key)
            for batch upload.

        Note:
            Only successfully saved PDFs appear in `path_mapping`. Failed saves during
            ``_save_pdfs_to_disk()`` are excluded from the mapping. This method silently
            skips missing files and invalid keys, logging warnings instead of
            raising errors.
        """
        pdfs: list[tuple[bytes, str, str, str]] = []

        for key, file_path in path_mapping.items():
            try:
                # Parse key to extract item_key and attachment_key
                # Try new delimiter first, fall back to old format
                if ":::" in key:
                    parts = key.split(":::", 1)
                else:
                    # Backward compatibility: fall back to old underscore delimiter
                    parts = key.rsplit("_", 1)
                    self.logger.warning(
                        f"Using legacy underscore delimiter for key: {key}. "
                        f"Consider regenerating path mappings with new delimiter."
                    )

                if len(parts) != 2:
                    self.logger.warning(
                        f"Invalid path mapping key format: {key}, skipping"
                    )
                    continue

                item_key, attachment_key = parts

                # Convert file path to Path object
                path = Path(file_path)

                # Check if file exists
                if not path.exists():
                    self.logger.warning(f"PDF file not found: {file_path}, skipping")
                    continue

                # Read PDF bytes
                pdf_bytes = path.read_bytes()

                # Extract filename from path
                filename = path.name

                # Append tuple to pdfs list
                pdfs.append((pdf_bytes, filename, item_key, attachment_key))

            except OSError as e:
                self.logger.error(
                    f"Failed to read PDF file {file_path}: {e}", exc_info=True
                )
            except Exception as e:
                self.logger.error(
                    f"Unexpected error reading PDF file {file_path}: {e}", exc_info=True
                )

        return pdfs

    @staticmethod
    def _is_pdf_attachment(attachment: AttachmentInfo) -> bool:
        """Return True if attachment is a PDF by filename or contentType."""
        fn = (attachment.filename or "").lower()
        ct = (attachment.content_type or "").lower()
        return fn.endswith(".pdf") or ct == "application/pdf"

    def _tag_items_based_on_download(
        self,
        items: list[DiscoveredItem],
        download_summary: dict[str, int],
        path_mapping: dict[str, str],
        *,
        apply_processed_tag: bool = True,
    ) -> None:
        """Tag items based on download success/failure status.

        Adds error tags to items with failed downloads (if error tagging is enabled)
        and, when ``apply_processed_tag`` is True, adds the output (processed) tag
        to items with all attachments successfully downloaded.

        Args:
            items: List of item dictionaries from _discover_items().
            download_summary: Dictionary with 'downloaded', 'skipped', 'failed' counts.
            path_mapping: Dictionary mapping f"{item_key}_{attachment_key}" to
            file paths.
            apply_processed_tag: When False, skip applying the output (processed) tag
                to download-succeeded items.  Error tagging is unaffected.

        Note:
            When `items` is an empty list, the method returns immediately without making
            any Zotero API calls. This is a no-op scenario that doesn't raise errors.
        """
        # Build set of successfully downloaded item keys
        success_item_keys = set()
        for key in path_mapping:
            if ":::" in key:
                parts = key.split(":::", 1)
            else:
                parts = key.rsplit("_", 1)
                self.logger.warning(f"Using legacy underscore delimiter for key: {key}")

            if len(parts) == 2:
                item_key, _ = parts
                success_item_keys.add(item_key)

        # Build set of failed item keys
        # An item fails if any of its PDF attachments is not in path_mapping
        failed_item_keys = set()
        for item in items:
            pdf_attachments = [
                att for att in item.attachments if self._is_pdf_attachment(att)
            ]
            if not pdf_attachments:
                failed_item_keys.add(item.key)
                continue

            all_succeeded = True
            for attachment in pdf_attachments:
                mapping_key = f"{item.key}:::{attachment.key}"
                if mapping_key not in path_mapping:
                    all_succeeded = False
                    break

            if not all_succeeded:
                failed_item_keys.add(item.key)

        # Tag items
        error_succeeded = 0
        error_skipped = 0
        error_partial_failed = 0
        error_fully_failed = 0
        error_add_succeeded = 0
        error_add_failed = 0
        error_remove_succeeded = 0
        error_remove_failed = 0
        processed_succeeded = 0
        processed_skipped = 0
        processed_partial_failed = 0
        processed_fully_failed = 0
        processed_add_succeeded = 0
        processed_add_failed = 0
        processed_remove_succeeded = 0
        processed_remove_failed = 0

        def _record_error_outcome(tag_result: ProcessingTagResult) -> None:
            nonlocal error_succeeded, error_skipped, error_partial_failed
            nonlocal error_fully_failed, error_add_succeeded, error_add_failed
            nonlocal error_remove_succeeded, error_remove_failed
            (
                error_succeeded,
                error_skipped,
                error_partial_failed,
                error_fully_failed,
            ) = self._observe_processing_tag_result(
                tag_result,
                succeeded=error_succeeded,
                skipped=error_skipped,
                partial_failed=error_partial_failed,
                fully_failed=error_fully_failed,
            )
            error_add_succeeded += tag_result.add_succeeded
            error_add_failed += tag_result.add_failed
            error_remove_succeeded += tag_result.remove_succeeded
            error_remove_failed += tag_result.remove_failed

        def _record_processed_outcome(tag_result: ProcessingTagResult) -> None:
            nonlocal processed_succeeded, processed_skipped
            nonlocal processed_partial_failed, processed_fully_failed
            nonlocal processed_add_succeeded, processed_add_failed
            nonlocal processed_remove_succeeded, processed_remove_failed
            (
                processed_succeeded,
                processed_skipped,
                processed_partial_failed,
                processed_fully_failed,
            ) = self._observe_processing_tag_result(
                tag_result,
                succeeded=processed_succeeded,
                skipped=processed_skipped,
                partial_failed=processed_partial_failed,
                fully_failed=processed_fully_failed,
            )
            processed_add_succeeded += tag_result.add_succeeded
            processed_add_failed += tag_result.add_failed
            processed_remove_succeeded += tag_result.remove_succeeded
            processed_remove_failed += tag_result.remove_failed

        for item in items:
            try:
                if item.key in failed_item_keys:
                    tag_result = self._apply_processing_tags(
                        item.key, success=False
                    )
                    if tag_result.has_failures:
                        self.logger.warning(
                            f"Some tag operations failed for item {item.key}"
                        )
                    _record_error_outcome(tag_result)
                elif item.key in success_item_keys:
                    pdf_attachments = [
                        att for att in item.attachments
                        if self._is_pdf_attachment(att)
                    ]
                    if not pdf_attachments:
                        tag_result = self._apply_processing_tags(
                            item.key, success=False
                        )
                        if tag_result.has_failures:
                            self.logger.warning(
                                f"Some tag operations failed for item {item.key}"
                            )
                        _record_error_outcome(tag_result)
                        continue
                    all_attachments_succeeded = True
                    for attachment in pdf_attachments:
                        mapping_key = f"{item.key}:::{attachment.key}"
                        if mapping_key not in path_mapping:
                            all_attachments_succeeded = False
                            break

                    if all_attachments_succeeded and apply_processed_tag:
                        tag_result = self._apply_processing_tags(
                            item.key, success=True
                        )
                        if tag_result.has_failures:
                            self.logger.warning(
                                f"Some tag operations failed for item {item.key}"
                            )
                        _record_processed_outcome(tag_result)
                else:
                    if apply_processed_tag:
                        pdf_attachments = [
                            att for att in item.attachments
                            if self._is_pdf_attachment(att)
                        ]
                        if len(pdf_attachments) == 0:
                            tag_result = self._apply_processing_tags(
                                item.key, success=False
                            )
                            if tag_result.has_failures:
                                self.logger.warning(
                                    f"Some tag operations failed for item {item.key}"
                                )
                            _record_error_outcome(tag_result)
            except ZoteroClientError as e:
                self.logger.warning(f"Failed to tag item {item.key}: {e}")

        # Log summary
        self.logger.info(
            self._format_processing_tag_summary(
                "tagged with error outcome tags",
                succeeded=error_succeeded,
                skipped=error_skipped,
                partial_failed=error_partial_failed,
                fully_failed=error_fully_failed,
                add_succeeded=error_add_succeeded,
                add_failed=error_add_failed,
                remove_succeeded=error_remove_succeeded,
                remove_failed=error_remove_failed,
            )
        )
        self.logger.info(
            self._format_processing_tag_summary(
                "tagged with processed outcome tags",
                succeeded=processed_succeeded,
                skipped=processed_skipped,
                partial_failed=processed_partial_failed,
                fully_failed=processed_fully_failed,
                add_succeeded=processed_add_succeeded,
                add_failed=processed_add_failed,
                remove_succeeded=processed_remove_succeeded,
                remove_failed=processed_remove_failed,
            )
        )

    def _apply_tag_adding(
        self, items: list[DiscoveredItem]
    ) -> tuple[list[TagAddingResult], int]:
        """Apply per-item tags from assignments to items whose citation keys match.

        Iterates through items, matching each item's citation key against the
        keys in ``assignments`` (case-sensitive, whitespace-trimmed). For each
        match, attempts to add that item's assigned tags via the Zotero API.

        Args:
            items: List of DiscoveredItem objects from _discover_items().

        Returns:
            A 2-tuple of (results, no_key_count) where results is a list of
            TagAddingResult objects (one per matched item) and no_key_count is
            the number of items skipped because they had no citation key.
        """
        assignments = self.tag_adding_config.assignments
        results: list[TagAddingResult] = []
        no_key_count: int = 0

        if (
            self.tag_adding_config.replace_all_existing_tags
            and not self._replace_mode_logged
        ):
            self.logger.info(
                "Tag Adding is running in REPLACE mode: all existing tags on "
                "matched items will be removed before applying assigned tags."
            )
            self._replace_mode_logged = True

        for item in items:
            item_title = item.title
            item_key = item.key
            item_citation_key = item.citation_key or ""

            if not item_citation_key.strip():
                self.logger.debug(
                    f"Item '{item_title}' (key={item_key}) has no citation key, "
                    "skipping tag adding"
                )
                no_key_count += 1
                continue

            normalized_key = item_citation_key.strip()
            if normalized_key not in assignments:
                continue

            effective_tags = list(assignments[normalized_key])
            tags_added: list[str] = []
            tags_failed: list[str] = []

            if self.tag_adding_config.replace_all_existing_tags:
                try:
                    self.zotero_client.set_tags(item_key, effective_tags)
                    tags_added = list(effective_tags)
                    tags_failed = []
                except ZoteroClientError as e:
                    self.logger.warning(
                        f"ZoteroClientError replacing tags on {item_key}: {e}"
                    )
                    tags_added = []
                    tags_failed = list(effective_tags)
                except Exception as e:
                    self.logger.warning(
                        f"Unexpected error replacing tags on {item_key}: {e}",
                        exc_info=True,
                    )
                    tags_added = []
                    tags_failed = list(effective_tags)
            else:
                for tag in effective_tags:
                    try:
                        self.zotero_client.add_tag(item_key, tag)
                        tags_added.append(tag)
                    except ZoteroClientError as e:
                        self.logger.warning(
                            f"ZoteroClientError adding tag '{tag}' to {item_key}: {e}"
                        )
                        tags_failed.append(tag)
                    except Exception as e:
                        self.logger.warning(
                            f"Unexpected error adding tag '{tag}' to {item_key}: {e}",
                            exc_info=True,
                        )
                        tags_failed.append(tag)

            result = TagAddingResult(
                item_key=item_key,
                item_title=item_title,
                matched=True,
                tags_added=tags_added,
                tags_failed=tags_failed,
            )
            log_tag_adding_result(self.logger, result)
            results.append(result)

        return results, no_key_count

    def _apply_output_tag_to_eligible_items(
        self,
        items: list[DiscoveredItem],
        tag_adding_results: list[TagAddingResult],
    ) -> int:
        """Apply processing tags using the original eligible item list.

        Matched items with all assigned tags succeeded receive success
        processing tags. Matched items with any failed assignments receive
        error processing tags. Unmatched items, including those without
        citation keys, receive success processing tags.

        Returns the number of items that received success processing tags.
        """
        processed_count = 0
        results_by_item_key = {
            result.item_key: result for result in tag_adding_results
        }

        for item in items:
            matched_result = results_by_item_key.get(item.key)
            item_matched = matched_result is not None
            item_succeeded = item_matched and not matched_result.tags_failed

            if item_matched and not item_succeeded:
                tag_result = self._apply_processing_tags(item.key, success=False)
                if tag_result.has_failures:
                    self.logger.warning(
                        f"Some tag operations failed for item {item.key}"
                    )
            else:
                tag_result = self._apply_processing_tags(item.key, success=True)
                if tag_result.has_failures:
                    self.logger.warning(
                        f"Some tag operations failed for item {item.key}"
                    )
                if tag_result.all_operations_succeeded:
                    processed_count += 1
                elif not tag_result.had_planned_work:
                    self.logger.debug(
                        f"Skipped processed outcome tags for item {item.key} "
                        "(no tag operations planned)"
                    )
                elif tag_result.had_partial_success:
                    self.logger.warning(
                        f"Partial processed outcome tag failure for item {item.key}"
                    )
                else:
                    self.logger.warning(
                        f"Processed outcome tagging failed for item {item.key}"
                    )

        return processed_count

    @staticmethod
    def _merge_disk_backed_pdfs(
        pdfs: list[tuple[bytes, str, str, str]],
        disk_pdfs: list[tuple[bytes, str, str, str]],
    ) -> int:
        """Supplement missing in-memory PDFs with disk-backed entries."""
        known_pdf_keys = {
            (item_key, attachment_key)
            for _, _, item_key, attachment_key in pdfs
        }
        supplemented_count = 0

        for disk_pdf in disk_pdfs:
            _, _, item_key, attachment_key = disk_pdf
            pdf_key = (item_key, attachment_key)
            if pdf_key in known_pdf_keys:
                continue

            pdfs.append(disk_pdf)
            known_pdf_keys.add(pdf_key)
            supplemented_count += 1

        return supplemented_count

    def _collect_all_pdfs(
        self, items: list[DiscoveredItem]
    ) -> list[tuple[bytes, str, str, str]]:
        """Collect all PDFs from items for batch processing.

        Iterates through all discovered items and downloads their PDF attachments.
        Collects PDFs into a list of 4-tuples (pdf_bytes, filename, item_key,
        attachment_key)
        for batch upload. Metadata is embedded directly in the tuples, eliminating the
        need for separate metadata tracking.

        Args:
            items: List of DiscoveredItem objects from _discover_items().

        Returns:
            List of 4-tuples (pdf_bytes, filename, item_key, attachment_key)
            for batch upload.
            Each tuple contains:
            - pdf_bytes: PDF file content as bytes
            - filename: Name of the PDF file
            - item_key: Zotero item key for tracking
            - attachment_key: Zotero attachment key for tracking

        This method implements per-PDF error isolation where individual download
        failures don't stop the entire batch. Failed downloads are logged and skipped.
        """
        pdfs: list[tuple[bytes, str, str, str]] = []

        # Calculate total PDFs across all items (PDF attachments only)
        total_pdfs = sum(
            len(
                [
                    att
                    for att in item.attachments
                    if self._is_pdf_attachment(att)
                ]
            )
            for item in items
        )

        success_count = 0
        total_count = total_pdfs

        # Wrap download loop with ProgressBar context manager
        with ProgressBar(total=total_pdfs, desc="Downloading PDFs", unit="pdf") as pbar:
            for item in items:
                item_key = item.key
                item_title = item.title
                pdf_attachments = [
                    att for att in item.attachments if self._is_pdf_attachment(att)
                ]

                for attachment in pdf_attachments:
                    attachment_key = attachment.key
                    filename = attachment.filename or "unknown.pdf"

                    try:
                        # Download PDF
                        pdf_bytes = self.zotero_client.download_pdf(
                            item_key, attachment_key
                        )

                        # Add to PDFs list as 4-tuple with embedded metadata
                        pdfs.append((pdf_bytes, filename, item_key, attachment_key))

                        success_count += 1

                    except ZoteroClientError as e:
                        # Log error with context
                        context = {
                            "item_key": item_key,
                            "item_title": item_title,
                            "attachment_key": attachment_key,
                            "filename": filename,
                            "step": "PDF Download",
                        }
                        log_error(self.logger, e, context)
                        # Continue to next PDF without stopping batch

                    # Update progress bar after each PDF attempt (even on failure)
                    pbar.update(1)

        # Log summary
        self.logger.info(f"Downloaded {success_count}/{total_count} PDFs successfully")

        return pdfs

    def _upload_pdfs_batch(
        self, pdfs: list[tuple[bytes, str, str, str]]
    ) -> dict[str, list[UploadedDocument]]:
        """Upload all PDFs in batch and build result mapping.

        Uploads all collected PDFs via OCR client's batch upload method and
        builds a mapping from item_key to list of UploadedDocument objects.
        This mapping is used for distributing OCR results back to items in later phases.
        Metadata is embedded in the 4-tuples and set directly during upload, eliminating
        the need for post-upload matching.

        Args:
            pdfs: List of 4-tuples (pdf_bytes, filename, item_key, attachment_key)
                from _collect_all_pdfs().

        Returns:
            Dictionary mapping item_key → List[UploadedDocument] for result
                distribution.
            Items with no successful uploads will have empty or missing entries.

        This method handles upload failures gracefully. If upload_pdfs_batch()
        returns fewer documents than input PDFs, missing uploads are already
        logged by the OCR client implementation.
        """
        # Initialize result mapping
        result_map: dict[str, list[UploadedDocument]] = {}

        ocr = self.ocr_client
        if ocr is None:
            raise RuntimeError(
                "OCR client is not initialized but batch upload was invoked"
            )

        # Upload all PDFs in batch with progress tracking
        with ProgressBar(total=len(pdfs), desc="Uploading PDFs", unit="pdf") as pbar:
            # Call OCR client's batch upload method
            uploaded_docs = ocr.upload_pdfs_batch(pdfs)

            # Update progress bar after completion (synchronous upload)
            pbar.update(len(uploaded_docs))

        # Build result mapping directly from uploaded documents
        # Metadata is already set on UploadedDocument objects from the 4-tuples
        for uploaded_doc in uploaded_docs:
            if uploaded_doc.item_key:
                result_map.setdefault(uploaded_doc.item_key, []).append(uploaded_doc)

        # Handle upload failures gracefully
        if len(uploaded_docs) < len(pdfs):
            self.logger.warning(
                f"Upload returned fewer documents ({len(uploaded_docs)}) than "
                f"input PDFs ({len(pdfs)}). Missing uploads logged by OCR client."
            )

        # Log summary
        self.logger.info(f"Uploaded {len(uploaded_docs)}/{len(pdfs)} PDFs successfully")

        return result_map

    def _poll_ocr_results_batch(
        self, uploaded_docs: list[UploadedDocument]
    ) -> dict[str, list[PageContent]]:
        """Poll OCR results for all uploaded documents with real-time progress tracking.

        This method orchestrates the batch polling of OCR results for all uploaded
        documents. It delegates the actual polling to the OCR client implementation
        (either MistralClient or PageIndexClient) while providing enhanced progress
        tracking and per-document status display.

        The method implements real-time progress updates through a progress callback
        that reports per-document status transitions (queued → processing →
        completed/failed). The progress bar displays live counts of queued,
        processing, and completed documents
        as polling progresses.

        The method follows per-document isolation where individual document
        failures don't
        stop the batch. Failed documents are logged but excluded from results, allowing
        the batch to complete successfully even if some documents fail.

        Args:
            uploaded_docs: List of UploadedDocument objects from `_upload_pdfs_batch()`
                containing doc_id, filename, and metadata.

        Returns:
            Dictionary mapping doc_id → List[PageContent] for successfully processed
            documents. Failed documents are logged but excluded from results
            (per-document isolation).

        Example:
            >>> uploaded_docs = [
            ...     UploadedDocument(doc_id="123", filename="doc.pdf", ...), ...
            ... ]
            >>> results = pipeline._poll_ocr_results_batch(uploaded_docs)
            >>> print(f"Processed {len(results)} documents")
            >>> for doc_id, pages in results.items():
            ...     print(f"Document {doc_id}: {len(pages)} pages")
        """
        # Handle empty input
        if not uploaded_docs:
            self.logger.info("No documents to poll for OCR results")
            return {}

        # Extract document IDs and initialize tracking
        doc_ids = [doc.doc_id for doc in uploaded_docs]
        doc_id_to_filename = {doc.doc_id: doc.filename for doc in uploaded_docs}
        doc_status: dict[str, str] = dict.fromkeys(doc_ids, "queued")

        # Log the start of polling phase
        self.logger.info(f"Starting OCR polling for {len(doc_ids)} documents")

        # Initialize OCR results dictionary
        ocr_results: dict[str, list[PageContent]] = {}

        # Implement real-time progress bar display with callback
        with ProgressBar(
            total=len(doc_ids), desc="Polling OCR results", unit="doc"
        ) as pbar:
            # Define progress callback that updates status and progress bar
            def progress_callback(doc_id: str, status: str) -> None:
                """Update document status and refresh progress bar display.

                Args:
                    doc_id: Document identifier
                    status: Status string ("processing", "completed", or "failed")
                """
                # Update document status
                doc_status[doc_id] = status

                # Count documents by status
                queued_count = sum(1 for s in doc_status.values() if s == "queued")
                processing_count = sum(
                    1 for s in doc_status.values() if s == "processing"
                )
                completed_count = sum(
                    1 for s in doc_status.values() if s == "completed"
                )
                failed_count = sum(1 for s in doc_status.values() if s == "failed")

                # Update progress bar for terminal states (completed or failed)
                if status in ("completed", "failed"):
                    pbar.update(1)

                # Refresh postfix with current counts
                pbar.set_postfix(
                    {
                        "Queued": queued_count,
                        "Processing": processing_count,
                        "Completed": completed_count,
                        "Failed": failed_count,
                    }
                )

            # Initial status (all documents queued)
            pbar.set_postfix(
                {"Completed": 0, "Processing": 0, "Queued": len(doc_ids), "Failed": 0}
            )

            # Call OCR client batch polling method with progress callback
            ocr = self.ocr_client
            if ocr is None:
                raise RuntimeError(
                    "OCR client is not initialized but batch polling was invoked"
                )
            try:
                ocr_results = ocr.poll_ocr_results_batch(
                    doc_ids, progress_callback
                )
            except Exception as e:
                # Log unexpected exceptions from OCR client
                self.logger.error(
                    f"Unexpected error during OCR polling: {e}", exc_info=True
                )
                # Return empty dict on exception (graceful degradation)
                return {}

        # Identify failed documents (those not in results)
        failed_docs = set(doc_ids) - set(ocr_results.keys())

        # Implement per-document error handling
        for doc_id in failed_docs:
            filename = doc_id_to_filename.get(doc_id, "unknown")
            context = {
                "doc_id": doc_id,
                "filename": filename,
                "step": "OCR Polling",
            }
            log_error(
                self.logger,
                Exception(f"OCR polling failed for document {doc_id}"),
                context,
            )

        # Log summary and return results
        success_count = len(ocr_results)
        failure_count = len(failed_docs)
        self.logger.info(
            f"OCR polling completed: {success_count}/{len(doc_ids)} documents "
            f"processed successfully"
        )

        if failure_count > 0:
            failed_filenames = [doc_id_to_filename[doc_id] for doc_id in failed_docs]
            self.logger.warning(
                f"Failed to process {failure_count} documents: {failed_filenames}"
            )

        return ocr_results

    def _extract_tree_structures(
        self,
        uploaded_docs_map: dict[str, list[UploadedDocument]],
        ocr_results: dict[str, list[PageContent]],
    ) -> dict[str, DocumentTree]:
        """Extract tree structures for all uploaded documents.

        This method extracts tree structures from documents that have been processed
        by OCR. Tree extraction is only performed if tree_structure.enabled is true
        and TreeStructureProcessor is initialized.

        This method implements per-document error isolation where individual tree
        extraction failures don't stop the batch. Failed documents are removed from
        ocr_results so Phase 3 will treat them as failed, allowing the pipeline to
        continue processing other documents.

        Args:
            uploaded_docs_map: Dictionary mapping item_key → List[UploadedDocument]
                from _upload_pdfs_batch().
            ocr_results: Dictionary mapping doc_id → List[PageContent] from
                _poll_ocr_results_batch(). Modified in-place: failed doc_ids are
                removed from this dictionary.

        Returns:
            Dictionary mapping doc_id → DocumentTree for successfully extracted trees.
            Empty dictionary if tree extraction is disabled or fails.

        Note:
            Provider-specific tree extraction routing eliminates redundant API calls.
            PageIndex: Trees are pre-extracted from OCR results (no additional
            API calls needed). Mistral: Trees must be generated from markdown
            using PageIndex API (requires PageIndex credentials). This routing
            prevents duplicate tree extraction in batch mode and optimizes API
            usage.
        """
        tree_structures: dict[str, DocumentTree] = {}

        # Skip if tree processor is not initialized
        if not self.tree_processor:
            return tree_structures

        # Collect all doc_ids from uploaded documents
        doc_ids = []
        for uploaded_docs in uploaded_docs_map.values():
            for uploaded_doc in uploaded_docs:
                if uploaded_doc.doc_id and uploaded_doc.doc_id not in doc_ids:
                    doc_ids.append(uploaded_doc.doc_id)

        if not doc_ids:
            return tree_structures

        self.logger.info(f"Extracting tree structures for {len(doc_ids)} documents")

        # Detect OCR provider for routing to appropriate extraction method
        if self.ocr_config.provider == "pageindex":
            provider = OCRProvider.PAGEINDEX
        elif self.ocr_config.provider == "mistral":
            provider = OCRProvider.MISTRAL
        else:
            self.logger.error(
                f"Unknown OCR provider in config: {self.ocr_config.provider!r}, "
                "skipping tree extraction"
            )
            return tree_structures
        provider_name = "PageIndex" if provider == OCRProvider.PAGEINDEX else "Mistral"
        self.logger.debug(f"Using {provider_name} tree extraction method")

        # Extract tree structure for each document with per-document error isolation
        failed_doc_ids = []
        with ProgressBar(
            total=len(doc_ids), desc="Extracting tree structures", unit="document"
        ) as pbar:
            for doc_id in doc_ids:
                try:
                    # Only extract trees for documents that have OCR results
                    if doc_id not in ocr_results:
                        pbar.update(1)
                        continue

                    if provider == OCRProvider.PAGEINDEX:
                        # PageIndex provides pre-extracted trees directly from
                        # OCR results. No additional API calls needed - trees
                        # are already available in the OCR response.
                        # PageIndex: Use existing extract_from_ocr_result method
                        tree = self.tree_processor.extract_from_ocr_result(doc_id)
                        tree_structures[doc_id] = tree
                        self.logger.debug(
                            f"Successfully extracted tree structure for doc_id "
                            f"{doc_id} using {provider_name}"
                        )
                    elif provider == OCRProvider.MISTRAL:
                        # Mistral only provides markdown content - trees must
                        # be generated separately. Generate trees from
                        # concatenated markdown using PageIndex API. REQUIRES:
                        # PageIndex API credentials (PAGEINDEX_API_KEY
                        # environment variable). This dependency is validated
                        # at startup (see run() method line 984-996).
                        # Mistral: Concatenate markdown from pages and process
                        pages = ocr_results[doc_id]

                        # Concatenate markdown from all pages
                        markdown = "\n\n".join(page.markdown for page in pages)

                        # Check for empty markdown
                        if not markdown or not markdown.strip():
                            self.logger.warning(
                                f"Empty markdown for doc_id {doc_id}, skipping "
                                f"tree extraction"
                            )
                            pbar.update(1)
                            continue

                        # Find filename from uploaded_docs_map
                        filename = doc_id  # Fallback to doc_id
                        for uploaded_docs in uploaded_docs_map.values():
                            for uploaded_doc in uploaded_docs:
                                if uploaded_doc.doc_id == doc_id:
                                    filename = uploaded_doc.filename
                                    break
                            if filename != doc_id:
                                break

                        # Process markdown to tree structure
                        tree = self.tree_processor.process_from_markdown(
                            markdown, filename
                        )
                        tree_structures[doc_id] = tree
                        self.logger.debug(
                            f"Successfully extracted tree structure for doc_id "
                            f"{doc_id} using {provider_name}"
                        )
                    else:
                        # Unknown provider - log error and skip
                        self.logger.error(
                            f"Unknown OCR provider: {provider}, skipping tree "
                            f"extraction for doc_id {doc_id}"
                        )
                        failed_doc_ids.append(doc_id)
                        pbar.update(1)
                        continue

                except TreeStructureProcessingError as e:
                    context = {"doc_id": doc_id, "step": "Tree Extraction"}
                    log_error(self.logger, e, context)
                    # Track failed doc_id for removal from ocr_results
                    failed_doc_ids.append(doc_id)
                    # Continue processing other documents (per-document isolation)
                except Exception as e:
                    wrapped = TreeStructureProcessingError(
                        f"Failed to extract tree structure for doc_id {doc_id}: "
                        f"{str(e)}",
                        original_exception=e,
                    )
                    context = {"doc_id": doc_id, "step": "Tree Extraction"}
                    log_error(self.logger, wrapped, context)
                    # Track failed doc_id for removal from ocr_results
                    failed_doc_ids.append(doc_id)
                    # Continue processing other documents (per-document isolation)

                # Update progress bar after each document (both success and failure)
                pbar.update(1)

        # Remove failed doc_ids from ocr_results so Phase 3 treats them as failed
        for doc_id in failed_doc_ids:
            if doc_id in ocr_results:
                del ocr_results[doc_id]
                self.logger.warning(
                    f"Removed doc_id {doc_id} from OCR results due to tree "
                    f"extraction failure"
                )

        self.logger.info(
            f"Extracted tree structures for {len(tree_structures)}/"
            f"{len(doc_ids)} documents"
        )

        if failed_doc_ids:
            self.logger.warning(
                f"Tree extraction failed for {len(failed_doc_ids)} document(s): "
                f"{failed_doc_ids}"
            )

        return tree_structures

    def _distribute_results_to_items(
        self,
        items: list[DiscoveredItem],
        uploaded_docs_map: dict[str, list[UploadedDocument]],
        ocr_results: dict[str, list[PageContent]],
        tree_structures: dict[str, DocumentTree],
    ) -> list[ProcessingResult]:
        """Map OCR results back to items and invoke processor with pre-fetched results.

        This method distributes batch OCR results back to individual items by:
        1. Mapping uploaded documents to their source items using uploaded_docs_map
        2. Building per-item OCR results dictionaries (attachment_key → PageContent)
        3. Invoking processor with pre-fetched results to skip redundant
        download/upload/OCR

        The method implements per-item error isolation where individual item
        failures don't stop the batch. Failed items are logged and tracked
        without affecting others.

        Args:
            items: List of DiscoveredItem objects from _discover_items().
            uploaded_docs_map: Dictionary mapping item_key → List[UploadedDocument]
                from _upload_pdfs_batch().
            ocr_results: Dictionary mapping doc_id → List[PageContent] from
                _poll_ocr_results_batch().
            tree_structures: Dictionary mapping doc_id → DocumentTree from
                _extract_tree_structures().

        Returns:
            List of ProcessingResult objects, one per item. Items with no uploaded
            documents or missing OCR results will have failed ProcessingResult entries.
        """
        results: list[ProcessingResult] = []
        success_count = 0
        failure_count = 0

        # Check if entire batch upload failed (no items have uploaded documents)
        batch_upload_failed = not uploaded_docs_map or not any(
            uploaded_docs_map.values()
        )
        batch_error_msg = (
            "Batch upload failed"
            if batch_upload_failed
            else "No uploaded documents found"
        )

        # Process items with progress tracking
        with ProgressBar(
            total=len(items), desc="Distributing results to items", unit="item"
        ) as pbar:
            for item in items:
                item_key = item.key
                item_title = item.title

                try:
                    # Check if item has uploaded documents
                    if (
                        item_key not in uploaded_docs_map
                        or not uploaded_docs_map[item_key]
                    ):
                        self.logger.warning(
                            f"No uploaded documents found for item {item_key} "
                            f"({item_title})"
                        )
                        failed_result = ProcessingResult(
                            item_key=item_key,
                            item_title=item_title,
                            success=False,
                            pdfs_processed=0,
                            pages_extracted=0,
                            notes_created=0,
                            errors=[batch_error_msg],
                            processing_time=0.0,
                            paper_metadata=item.paper_metadata,
                        )
                        results.append(failed_result)
                        failure_count += 1
                        pbar.update(1)
                        continue

                    # Build item_ocr_results dictionary: attachment_key →
                    # List[PageContent]
                    uploaded_docs = uploaded_docs_map[item_key]
                    item_ocr_results: dict[str, list[PageContent]] = {}
                    pre_extracted_trees: dict[str, DocumentTree] = {}

                    for uploaded_doc in uploaded_docs:
                        doc_id = uploaded_doc.doc_id
                        attachment_key = uploaded_doc.attachment_key
                        filename = uploaded_doc.filename

                        # Look up OCR results for this document
                        if doc_id not in ocr_results:
                            self.logger.warning(
                                f"No OCR results found for document {doc_id} "
                                f"(filename: {filename})"
                            )
                            # Continue - processor will handle missing results
                            # gracefully
                            continue

                        # Map attachment_key to OCR results (using doc_id as
                        # fallback if attachment_key is None)
                        key = attachment_key if attachment_key else doc_id
                        item_ocr_results[key] = ocr_results[doc_id]

                        # Map tree structures: join uploaded_docs_map doc_ids
                        # to tree_structures
                        # Key by attachment_key (same key used in item_ocr_results)
                        if doc_id in tree_structures:
                            pre_extracted_trees[key] = tree_structures[doc_id]

                    # Process item with pre-fetched OCR results and tree structures
                    result = self.processor.process_item(
                        item,
                        ocr_results=item_ocr_results,
                        pre_extracted_trees=pre_extracted_trees
                        if pre_extracted_trees
                        else None,
                    )
                    result.paper_metadata = item.paper_metadata
                    results.append(result)

                    if result.success:
                        success_count += 1
                    else:
                        failure_count += 1

                except Exception as e:
                    # Catch any unexpected exceptions during result distribution
                    context = {
                        "item_key": item_key,
                        "item_title": item_title,
                        "step": "Result Distribution",
                    }
                    log_error(self.logger, e, context)

                    # Create failed ProcessingResult
                    failed_result = ProcessingResult(
                        item_key=item_key,
                        item_title=item_title,
                        success=False,
                        pdfs_processed=0,
                        pages_extracted=0,
                        notes_created=0,
                        errors=[str(e)],
                        processing_time=0.0,
                        paper_metadata=item.paper_metadata,
                    )
                    results.append(failed_result)
                    failure_count += 1

                # Update progress bar
                pbar.update(1)

        # Log summary
        self.logger.info(
            f"Distributed results to {success_count}/{len(items)} items successfully"
        )

        return results

    def _cleanup_uploaded_documents(
        self, uploaded_docs_map: dict[str, list[UploadedDocument]]
    ) -> None:
        """Cleanup OCR files for all uploaded documents.

        Iterates over all UploadedDocument.doc_id values from uploaded_docs_map
        and calls ocr_client.cleanup_file() for each, logging any failures but
        not aborting the pipeline.

        Args:
            uploaded_docs_map: Dictionary mapping item_key → List[UploadedDocument]
                containing all uploaded documents that need cleanup.
        """
        # Collect all doc_ids from uploaded_docs_map
        doc_ids_to_cleanup: list[str] = []
        for uploaded_docs in uploaded_docs_map.values():
            for uploaded_doc in uploaded_docs:
                if uploaded_doc.doc_id:
                    doc_ids_to_cleanup.append(uploaded_doc.doc_id)

        if not doc_ids_to_cleanup:
            self.logger.debug("No uploaded documents to cleanup")
            return

        self.logger.info(f"Cleaning up {len(doc_ids_to_cleanup)} uploaded OCR files")

        # Cleanup each document, logging failures but not aborting
        cleanup_success_count = 0
        cleanup_failure_count = 0

        ocr = self.ocr_client
        if ocr is None:
            self.logger.warning(
                "Skipping OCR file cleanup: OCR client is not initialized"
            )
            return

        for doc_id in doc_ids_to_cleanup:
            try:
                ocr.cleanup_file(doc_id)
                cleanup_success_count += 1
            except Exception as cleanup_error:
                cleanup_failure_count += 1
                self.logger.warning(
                    f"Failed to cleanup OCR file {doc_id}: {cleanup_error}"
                )

        # Log cleanup summary
        if cleanup_failure_count > 0:
            self.logger.warning(
                f"OCR cleanup completed with {cleanup_failure_count} failure(s): "
                f"{cleanup_success_count} succeeded, {cleanup_failure_count} failed"
            )
        else:
            self.logger.info(
                f"Successfully cleaned up {cleanup_success_count} OCR files"
            )

    def _save_to_disk(
        self,
        item: DiscoveredItem,
        result: ProcessingResult,
        item_trees: dict[str, DocumentTree] | None = None,
    ) -> None:
        """Save processing results to disk for debugging.

        Creates a directory structure for each item and saves:
        1. Processing summary as JSON: {base_dir}/{item_key}/processing_summary.json
        2. Markdown content files: {base_dir}/{item_key}/{pdf_name}_page_{n}.md
        3. Tree structure files: {base_dir}/{item_key}/{pdf_name}
        _tree_structure.json (if available)

        The markdown files contain the raw extracted markdown content from each page,
        which is useful for debugging and reviewing OCR results before they are
        converted to HTML notes in Zotero.

        Args:
            item: DiscoveredItem with item metadata (key, title, tags, attachments).
            result: ProcessingResult object with processing metrics, errors,
            and page contents. item_trees: Optional dictionary mapping
            attachment_key → DocumentTree for tree structures
                to save to disk. If None or empty, tree structures are not saved.

        Returns:
            None. Errors are logged as warnings but don't raise exceptions (disk
            save is optional and shouldn't fail the pipeline).
        """
        try:
            # Create base directory
            base_dir = Path(self.storage_config.base_dir)
            base_dir.mkdir(parents=True, exist_ok=True)

            # Create item-specific subdirectory
            safe_key = item.key.replace("/", "_")
            item_dir = base_dir / safe_key
            item_dir.mkdir(parents=True, exist_ok=True)

            # Save processing summary
            summary: dict[str, Any] = {
                "item_key": item.key,
                "item_title": item.title,
                "success": result.success,
                "pdfs_processed": result.pdfs_processed,
                "pages_extracted": result.pages_extracted,
                "notes_created": result.notes_created,
                "errors": result.errors,
                "processing_time": result.processing_time,
                "paper_metadata": item.paper_metadata.to_dict(),
            }

            summary_path = item_dir / "processing_summary.json"
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            self.logger.debug(f"Saved processing summary to {summary_path}")

            # Map attachment_key → filename for safe filenames on disk
            attachment_filename_map: dict[str, str] = {
                att.key: att.filename
                for att in item.attachments
                if att.key and att.filename
            }

            # Save markdown content files if available
            if result.page_contents:
                for attachment_key, page_contents_list in result.page_contents.items():
                    pdf_filename = attachment_filename_map.get(attachment_key)
                    if not pdf_filename:
                        self.logger.warning(
                            f"No filename found for attachment {attachment_key}"
                        )
                        continue
                    # Create safe filename by removing/replacing problematic characters
                    safe_pdf_name = pdf_filename.replace("/", "_").replace("\\", "_")
                    # Remove .pdf extension if present for cleaner filenames
                    if safe_pdf_name.lower().endswith(".pdf"):
                        safe_pdf_name = safe_pdf_name[:-4]

                    # Write markdown file for each page
                    for page_content in page_contents_list:
                        # Create filename: {pdf_name}_page_{n}.md
                        markdown_filename = (
                            f"{safe_pdf_name}_page_{page_content.page_number}.md"
                        )
                        try:
                            markdown_path = item_dir / markdown_filename

                            # Write markdown content to file
                            markdown_path.write_text(
                                page_content.markdown, encoding="utf-8"
                            )
                            self.logger.debug(
                                f"Saved markdown content to {markdown_path}"
                            )
                        except OSError as e:
                            self.logger.warning(
                                f"Failed to save markdown file {markdown_filename} "
                                f"for item {item.key}: {e}"
                            )
                        except Exception as e:
                            self.logger.warning(
                                f"Unexpected error saving markdown file "
                                f"{markdown_filename} for item "
                                f"{item.key}: {e}"
                            )

            # Save tree structure files if available
            if item_trees and self.tree_processor:
                for attachment_key, tree in item_trees.items():
                    pdf_filename = attachment_filename_map.get(attachment_key)
                    if not pdf_filename:
                        self.logger.warning(
                            f"No filename found for attachment {attachment_key}, "
                            "skipping tree structure save"
                        )
                        continue
                    try:
                        # Save tree structure using
                        # TreeStructureProcessor.save_to_disk()
                        self.tree_processor.save_to_disk(tree, item_dir, pdf_filename)
                        self.logger.debug(
                            f"Saved tree structure for {pdf_filename} to {item_dir}"
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to save tree structure for {pdf_filename} "
                            f"for item {item.key}: {e}"
                        )
        except OSError as e:
            self.logger.warning(
                f"Failed to save processing summary to disk for item "
                f"{item.key}: {e}"
            )
        except Exception as e:
            self.logger.warning(
                f"Unexpected error saving processing summary to disk for item "
                f"{item.key}: {e}"
            )

    def run(self) -> dict[str, Any]:
        """Execute the complete processing pipeline using batch processing.

        This method orchestrates the end-to-end batch processing workflow:
        1. Discovers items using tag-based filtering
        2. Phase 1: Collects and uploads all PDFs in batch
        3. Phase 2: Polls OCR results for all uploaded documents in batch
        4. Phase 3: Distributes OCR results to items and processes them
        5. Handles per-item success/failure with appropriate tagging
        6. Optionally saves results to disk
        7. Aggregates results for summary reporting

        The method implements graceful error handling where individual item
        failures don't stop the entire pipeline. Each phase has dedicated
        error handling with per-phase isolation. Failed phases log errors
        and return early with partial results when appropriate.

        Returns:
            Dictionary containing aggregated summary with keys:
            - total_items: Total number of items processed
            - successful_items: Count of successfully processed items
            - failed_items: Count of failed items
            - total_pdfs_processed: Sum of all pdfs_processed across all items
            - total_pages_extracted: Sum of all pages_extracted across all items
            - total_notes_created: Sum of all notes_created across all items
            - results: List of all ProcessingResult objects for detailed reporting

        Example:
            >>> summary = pipeline.run()
            >>> print(f"Total: {summary['total_items']}, "
            ...       f"Success: {summary['successful_items']}, "
            ...       f"Failed: {summary['failed_items']}")
            >>> for result in summary['results']:
            ...     if not result.success:
            ...         print(f"Failed: {result.item_title}")

        Note:
            File cleanup behavior is configurable with default retention behavior.
            Default: cleanup_uploaded_files=false (files retained in OCR
            provider storage). This allows re-processing, debugging, and cost
            management without automatic deletion.
            Set cleanup_uploaded_files=true to restore previous auto-delete behavior.
        """
        # Step 0: Log startup
        log_startup(self.logger, "Starting Zotero DocAI Pipeline")

        self._replace_mode_logged = False

        # Log file cleanup configuration status
        if self.processing_config.cleanup_uploaded_files:
            self.logger.info("File cleanup: ENABLED")
        else:
            self.logger.info("File cleanup: DISABLED")

        # Log tree extraction status
        if self.tree_processor is not None:
            self.logger.info("Tree structure extraction: ENABLED")
        else:
            self.logger.info("Tree structure extraction: DISABLED")

        # Log download status
        if self.download_config.enabled:
            self.logger.info(
                f"PDF download: ENABLED (folder: {self.download_config.upload_folder})"
            )
        else:
            self.logger.info("PDF download: DISABLED")

        # Log tag adding status
        if self.tag_adding_config.enabled:
            num_keys = len(self.tag_adding_config.assignments)
            total_tags = sum(
                len(tags) for tags in self.tag_adding_config.assignments.values()
            )
            self.logger.info(
                f"Tag Adding: ENABLED ({num_keys} citation keys, "
                f"{total_tags} total tag assignments)"
            )
        else:
            self.logger.info("Tag Adding: DISABLED")

        # Validate tree extraction configuration for Mistral OCR (OCR path only)
        if (
            self.tree_structure_config.enabled
            and self.ocr_config.enabled
            and self.ocr_config.provider == "mistral"
        ):
            # Check if PageIndex credentials are available via environment
            has_pageindex_env = bool(os.getenv("PAGEINDEX_API_KEY"))

            if not has_pageindex_env:
                self.logger.warning(
                    "Tree extraction enabled with Mistral OCR but PageIndex "
                    "credentials not available. Tree extraction requires "
                    "PageIndex API credentials. Set PAGEINDEX_API_KEY "
                    "environment variable or configure PageIndex OCR provider."
                )
                self.tree_processor = None
                self.processor.tree_processor = None

        # Step 0.5: Track start time for timing summary
        start_time = time.time()

        # Initialize download_summary for summary aggregation
        download_summary: dict[str, int] = {}

        # Step 1: Discover items
        items, discovery_stats = self._discover_items()

        if self.export_config.attachment_urls.enabled:
            records = build_export_records(items, self.zotero_client)
            if self.export_config.attachment_urls.auth_query.enabled:
                for rec in records:
                    auth_url = self.zotero_client.build_authenticated_attachment_url(
                        rec.attachment_key
                    )
                    self.logger.info(
                        "[AUTH QUERY URL] item_key=%s attachment_key=%s url=%s",
                        rec.item_key,
                        rec.attachment_key,
                        redact_url(auth_url),
                    )
            if self.export_config.attachment_urls.log:
                log_export_records(records, self.logger)
            if self.export_config.attachment_urls.write_manifest:
                write_manifest(
                    records, self.export_config.attachment_urls.manifest_path
                )

        # Step 2: Handle empty list
        if not items:
            self.logger.info("No items found to process")
            return {
                "total_items": 0,
                "successful_items": 0,
                "failed_items": 0,
                "skipped_items": discovery_stats.excluded_count,
                "matched_items": discovery_stats.matched_count,
                "excluded_by_rule": discovery_stats.excluded_by_rule,
                "total_pdfs_processed": 0,
                "total_pages_extracted": 0,
                "total_notes_created": 0,
                "total_time": 0.0,
                "results": [],
                "tag_adding_results": [],
                "tag_adding_failed": 0,
                "tag_adding_matched": 0,
                "tag_adding_succeeded": 0,
                "tag_adding_eligible": 0,
                "tag_adding_no_key": 0,
                "tag_adding_processed": 0,
            }

        # Early exit for standalone tag-adding mode
        if (
            self.tag_adding_config.enabled
            and not self.ocr_config.enabled
            and not self.download_config.enabled
        ):
            self.logger.info(
                "Standalone tag-adding mode: OCR and download disabled"
            )
            total_assigned_tags = sum(
                len(tags) for tags in self.tag_adding_config.assignments.values()
            )
            log_tag_adding_start(
                self.logger, len(items), total_assigned_tags
            )
            tag_adding_results, no_key_count = self._apply_tag_adding(items)

            tag_adding_processed = self._apply_output_tag_to_eligible_items(
                items, tag_adding_results
            )

            # Recompute after possible mutations
            tag_adding_failed = sum(
                1 for r in tag_adding_results if r.tags_failed
            )
            tag_adding_succeeded = sum(
                1 for r in tag_adding_results if not r.tags_failed
            )
            tag_adding_matched = len(tag_adding_results)
            total_time = time.time() - start_time

            unmatched = len(items) - tag_adding_matched
            successful_items = unmatched + tag_adding_succeeded
            failed_items = tag_adding_failed

            summary = {
                "total_items": len(items),
                "successful_items": successful_items,
                "failed_items": failed_items,
                "skipped_items": discovery_stats.excluded_count,
                "matched_items": discovery_stats.matched_count,
                "excluded_by_rule": discovery_stats.excluded_by_rule,
                "total_pdfs_processed": 0,
                "total_pages_extracted": 0,
                "total_notes_created": 0,
                "total_time": total_time,
                "results": [],
                "tag_adding_results": tag_adding_results,
                "tag_adding_failed": tag_adding_failed,
                "tag_adding_matched": tag_adding_matched,
                "tag_adding_succeeded": tag_adding_succeeded,
                "tag_adding_eligible": len(items),
                "tag_adding_no_key": no_key_count,
                "tag_adding_processed": tag_adding_processed,
            }

            # Log completion
            log_completion(self.logger)

            self.logger.info("=" * 80)
            self.logger.info(
                f"Pipeline completed in {total_time:.1f}s: "
                f"Tag Adding matched {tag_adding_matched}/{len(items)} items, "
                f"{tag_adding_succeeded} succeeded, {tag_adding_failed} failed"
            )
            self.logger.info("=" * 80)

            return summary

        # ========================================================================
        # Phase 1: Collect & Upload PDFs
        # ========================================================================
        self.logger.info("=" * 80)
        self.logger.info("Phase 1: Collecting and uploading PDFs")
        self.logger.info("=" * 80)
        phase1_start_time = time.time()

        pdfs = self._collect_all_pdfs(items)

        # ========================================================================
        # Phase 2.5: Download PDFs to Disk (if enabled)
        # ========================================================================
        if self.download_config.enabled:
            self.logger.info("=" * 80)
            self.logger.info("Phase 2.5: Downloading PDFs to disk")
            self.logger.info("=" * 80)
            phase2_5_start_time = time.time()

            try:
                download_summary, path_mapping = self._save_pdfs_to_disk(items, pdfs)
                self._download_path_mapping = path_mapping

                downloaded = download_summary.get("downloaded", 0)
                skipped = download_summary.get("skipped", 0)
                failed = download_summary.get("failed", 0)
                total = len(pdfs)

                phase2_5_time = time.time() - phase2_5_start_time
                self.logger.info(
                    f"Phase 2.5 completed in {phase2_5_time:.1f}s: "
                    f"{downloaded}/{total} PDFs saved "
                    f"(Skipped: {skipped}, Failed: {failed})"
                )
            except Exception as e:
                self.logger.error(f"Download phase failed: {e}", exc_info=True)
                self.logger.warning(
                    "Download phase failed, continuing with in-memory PDFs for OCR"
                )
                download_summary = {"downloaded": 0, "skipped": 0, "failed": len(pdfs)}
                self._download_path_mapping = {}

        # Early exit for download-only mode
        if self.download_config.enabled and not self.ocr_config.enabled:
            self.logger.info("Download-only mode: OCR disabled, skipping OCR phases")
            apply_processed_tag_on_download = not self.tag_adding_config.enabled
            self._tag_items_based_on_download(
                items, download_summary, self._download_path_mapping,
                apply_processed_tag=apply_processed_tag_on_download,
            )

            # Compute items that succeeded download (includes no-PDF items as
            # vacuously successful)
            path_mapping = self._download_path_mapping
            download_succeeded_items: list[DiscoveredItem] = []
            for item in items:
                pdf_attachments = [
                    att for att in item.attachments if self._is_pdf_attachment(att)
                ]
                if not pdf_attachments:
                    continue
                all_succeeded = True
                for att in pdf_attachments:
                    mapping_key = f"{item.key}:::{att.key}"
                    if mapping_key not in path_mapping:
                        all_succeeded = False
                        break
                if all_succeeded:
                    download_succeeded_items.append(item)

            # Tag adding in download-only mode
            tag_adding_results: list[TagAddingResult] = []
            tag_adding_failed = 0
            tag_adding_matched = 0
            tag_adding_succeeded = 0
            tag_adding_eligible = 0
            tag_adding_processed = 0
            no_key_count = 0

            if self.tag_adding_config.enabled:
                tag_adding_eligible = len(download_succeeded_items)
                total_assigned_tags = sum(
                    len(tags) for tags in self.tag_adding_config.assignments.values()
                )
                log_tag_adding_start(
                    self.logger,
                    tag_adding_eligible,
                    total_assigned_tags,
                )
                tag_adding_results, no_key_count = self._apply_tag_adding(
                    download_succeeded_items
                )
                tag_adding_processed = self._apply_output_tag_to_eligible_items(
                    download_succeeded_items,
                    tag_adding_results,
                )

                # Recompute after possible mutations
                tag_adding_failed = sum(
                    1 for r in tag_adding_results if r.tags_failed
                )
                tag_adding_succeeded = sum(
                    1 for r in tag_adding_results if not r.tags_failed
                )
                tag_adding_matched = len(tag_adding_results)

            # Return early with download-only summary
            total_time = time.time() - start_time
            successful_items = len(download_succeeded_items)
            failed_items = len(items) - successful_items
            summary = {
                "total_items": len(items),
                "successful_items": successful_items,
                "failed_items": failed_items,
                "skipped_items": discovery_stats.excluded_count,
                "matched_items": discovery_stats.matched_count,
                "excluded_by_rule": discovery_stats.excluded_by_rule,
                "total_pdfs_processed": 0,
                "total_pages_extracted": 0,
                "total_notes_created": 0,
                "total_time": total_time,
                "results": [],
                "total_pdfs_downloaded": download_summary.get("downloaded", 0),
                "total_pdfs_skipped": download_summary.get("skipped", 0),
                "total_pdfs_failed": download_summary.get("failed", 0),
            }

            summary["tag_adding_results"] = tag_adding_results
            summary["tag_adding_failed"] = tag_adding_failed
            summary["tag_adding_matched"] = tag_adding_matched
            summary["tag_adding_succeeded"] = tag_adding_succeeded
            summary["tag_adding_eligible"] = tag_adding_eligible
            summary["tag_adding_no_key"] = no_key_count
            summary["tag_adding_processed"] = tag_adding_processed

            # Log completion
            log_completion(self.logger)

            # Log summary statistics
            self.logger.info("=" * 80)
            self.logger.info(
                f"Pipeline completed in {total_time:.1f}s: "
                f"{summary['total_items']} items processed, "
                f"{summary['successful_items']} successful, "
                f"{summary['failed_items']} failed"
            )
            self.logger.info(
                f"Download: {summary['total_pdfs_downloaded']} downloaded, "
                f"{summary['total_pdfs_skipped']} skipped, "
                f"{summary['total_pdfs_failed']} failed"
            )
            self.logger.info("=" * 80)

            return summary

        # Read PDFs from disk if download is enabled
        if self.download_config.enabled and self._download_path_mapping:
            disk_pdfs = self._read_pdfs_from_disk(self._download_path_mapping)
            supplemented_pdfs = self._merge_disk_backed_pdfs(pdfs, disk_pdfs)
            self.logger.info(
                f"Read {len(disk_pdfs)} PDFs from disk for OCR upload; "
                f"supplemented {supplemented_pdfs} missing in-memory PDFs"
            )

        # Handle empty PDFs case
        if not pdfs:
            self.logger.warning(
                "No PDFs collected from items, skipping to Phase 3 with "
                "empty results"
            )
            phase1_time = time.time() - phase1_start_time
            self.logger.info(
                f"Phase 1 completed in {phase1_time:.1f}s: 0 PDFs uploaded"
            )
            ocr_results = {}
            uploaded_docs_map = {}
            self.uploaded_docs_map = uploaded_docs_map
            # Skip Phase 2 and go directly to Phase 3
        else:
            # Upload PDFs in batch
            uploaded_docs_map = self._upload_pdfs_batch(pdfs)
            self.uploaded_docs_map = uploaded_docs_map

            # Handle upload failure - create failed ProcessingResult
            # entries and continue
            if not uploaded_docs_map or not any(uploaded_docs_map.values()):
                self.logger.error("Batch upload failed: no documents uploaded")
                phase1_time = time.time() - phase1_start_time
                self.logger.info(
                    f"Phase 1 completed in {phase1_time:.1f}s: 0 PDFs uploaded"
                )
                # Set empty results to skip Phase 2 and proceed to Phase 3
                ocr_results = {}
                # Keep uploaded_docs_map empty so Phase 3 will create failed results
            else:
                # Extract all uploaded documents into flat list for Phase 2
                all_uploaded_docs = [
                    doc for docs in uploaded_docs_map.values() for doc in docs
                ]

                phase1_time = time.time() - phase1_start_time
                self.logger.info(
                    f"Phase 1 completed in {phase1_time:.1f}s: "
                    f"{len(all_uploaded_docs)} PDFs uploaded successfully"
                )

                # ===========================================================
                # Phase 2: Batch Poll OCR Results
                # ===========================================================
                self.logger.info("=" * 80)
                self.logger.info(
                    f"Phase 2: Polling OCR results for "
                    f"{len(all_uploaded_docs)} documents"
                )
                self.logger.info("=" * 80)
                phase2_start_time = time.time()

                ocr_results = self._poll_ocr_results_batch(all_uploaded_docs)

                # Handle polling failure (log warning but continue to Phase 3)
                phase2_time = time.time() - phase2_start_time
                if not ocr_results:
                    self.logger.warning(
                        "Batch polling failed: no OCR results available"
                    )
                    self.logger.info(
                        f"Phase 2 completed in {phase2_time:.1f}s: "
                        f"0/{len(all_uploaded_docs)} documents processed "
                        f"successfully"
                    )
                else:
                    success_count = len(ocr_results)
                    total_count = len(all_uploaded_docs)
                    self.logger.info(
                        f"Phase 2 completed in {phase2_time:.1f}s: "
                        f"{success_count}/{total_count} documents "
                        f"processed successfully"
                    )

        # ========================================================================
        # Phase 3: Extracting tree structures
        # ========================================================================
        # Extract tree structures after OCR results are available
        tree_structures: dict[str, DocumentTree] = {}

        if self.tree_processor is not None:
            self.logger.info("=" * 80)
            self.logger.info("Phase 3: Extracting tree structures")
            self.logger.info("=" * 80)
            phase3_start_time = time.time()

            if uploaded_docs_map:
                try:
                    tree_structures = self._extract_tree_structures(
                        uploaded_docs_map, ocr_results
                    )
                except TreeStructureProcessingError as e:
                    # Handle tree extraction failure: convert to per-document
                    # errors. Failed doc_ids are already removed from
                    # ocr_results by _extract_tree_structures(). Log the error
                    # and continue to Phase 4 so failed items are reported and
                    # tagged
                    context = {"step": "Tree Extraction"}
                    log_error(self.logger, e, context)
                    self.logger.warning(
                        "Tree extraction encountered errors, but continuing to "
                        "Phase 4 to report and tag failed items"
                    )
                    # tree_structures remains empty dict, Phase 4 will handle
                    # missing results
                except Exception as e:
                    # Handle unexpected errors during tree extraction
                    self.logger.error(
                        f"Unexpected error during tree extraction: {e}", exc_info=True
                    )
                    self.logger.warning(
                        "Continuing to Phase 4 despite tree extraction error "
                        "to ensure cleanup and error reporting"
                    )
                    # tree_structures remains empty dict, Phase 4 will handle
                    # missing results

            # Store tree_structures as instance variable for use in _save_to_disk()
            self._tree_structures = tree_structures

            # Calculate phase completion logging
            doc_ids = []
            if uploaded_docs_map:
                for uploaded_docs in uploaded_docs_map.values():
                    for uploaded_doc in uploaded_docs:
                        if uploaded_doc.doc_id and uploaded_doc.doc_id not in doc_ids:
                            doc_ids.append(uploaded_doc.doc_id)
            phase3_time = time.time() - phase3_start_time
            self.logger.info(
                f"Phase 3 completed in {phase3_time:.1f}s: "
                f"{len(tree_structures)}/{len(doc_ids)} trees extracted "
                f"successfully"
            )
        else:
            # Tree extraction disabled - set empty dict and skip Phase 3 entirely
            tree_structures = {}
            self._tree_structures = tree_structures

        # ========================================================================
        # Phase 4: Distribute Results to Items
        # ========================================================================
        self.logger.info("=" * 80)
        self.logger.info(f"Phase 4: Distributing results to {len(items)} items")
        self.logger.info("=" * 80)
        phase4_start_time = time.time()

        try:
            results = self._distribute_results_to_items(
                items, uploaded_docs_map, ocr_results, tree_structures
            )
        except Exception as e:
            # Handle distribution failure
            self.logger.error(
                f"Phase 4 failed with unexpected error: {e}", exc_info=True
            )
            # Create failed ProcessingResult entries for all items
            results = []
            for item in items:
                failed_result = ProcessingResult(
                    item_key=item.key,
                    item_title=item.title,
                    success=False,
                    pdfs_processed=0,
                    pages_extracted=0,
                    notes_created=0,
                    errors=[f"Result distribution failed: {e!s}"],
                    processing_time=0.0,
                    paper_metadata=item.paper_metadata,
                )
                results.append(failed_result)

            phase4_time = time.time() - phase4_start_time
            success_count = sum(1 for r in results if r.success)
            self.logger.info(
                f"Phase 4 completed in {phase4_time:.1f}s: "
                f"{success_count}/{len(items)} items processed successfully"
            )

        # ========================================================================
        # File Cleanup (if enabled)
        # ========================================================================
        if self.processing_config.cleanup_uploaded_files:
            # Count total documents for logging
            total_docs = sum(len(docs) for docs in self.uploaded_docs_map.values())
            self._cleanup_uploaded_documents(self.uploaded_docs_map)
            self.logger.info(f"Cleaned up {total_docs} files")
        else:
            # Count total documents for logging
            total_docs = sum(len(docs) for docs in self.uploaded_docs_map.values())
            self.logger.info(f"Files retained: {total_docs} documents")

        # ========================================================================
        # Post-Processing: Tagging and Disk Storage
        # ========================================================================
        self.logger.info("=" * 80)
        self.logger.info("Post-Processing: Tagging items and saving to disk")
        self.logger.info("=" * 80)

        # Build item lookup map for post-processing
        item_map: dict[str, DiscoveredItem] = {item.key: item for item in items}

        tag_adding_results_ocr: list[TagAddingResult] = []
        tag_adding_no_key_total: int = 0
        tag_adding_processed: int = 0

        for result in results:
            item = item_map.get(result.item_key)
            if not item:
                continue

            # Handle successful results
            if result.success:
                # Add 500ms delay after successful items with notes created
                # (for Zotero version propagation)
                if result.notes_created > 0:
                    time.sleep(0.5)

                if self.tag_adding_config.enabled:
                    # Step A: apply tag adding first
                    item_tag_results, item_no_key = self._apply_tag_adding([item])
                    tag_adding_no_key_total += item_no_key
                    if item_tag_results:
                        tag_adding_results_ocr.append(item_tag_results[0])

                    # Step B: apply processing tags based on tag-adding outcome.
                    item_matched = bool(item_tag_results)
                    item_succeeded = (
                        item_matched and not item_tag_results[0].tags_failed
                    )

                    if item_matched and not item_succeeded:
                        tag_result = self._apply_processing_tags(
                            item.key, success=False
                        )
                        if tag_result.has_failures:
                            self.logger.warning(
                                f"Some tag operations failed for item {item.key}"
                            )
                    else:
                        tag_result = self._apply_processing_tags(
                            item.key, success=True
                        )
                        if tag_result.has_failures:
                            self.logger.warning(
                                f"Some tag operations failed for item {item.key}"
                            )
                        if tag_result.all_operations_succeeded:
                            tag_adding_processed += 1
                        elif not tag_result.had_planned_work:
                            self.logger.debug(
                                f"Skipped processed outcome tags for item "
                                f"{item.key} (no tag operations planned)"
                            )
                        elif tag_result.had_partial_success:
                            self.logger.warning(
                                f"Partial processed outcome tag failure for item "
                                f"{item.key}"
                            )
                        else:
                            self.logger.warning(
                                f"Processed outcome tagging failed for item "
                                f"{item.key}"
                            )
                else:
                    tag_result = self._apply_processing_tags(
                        item.key, success=True
                    )
                    if tag_result.has_failures:
                        self.logger.warning(
                            f"Some tag operations failed for item {item.key}"
                        )

            # Handle failed results
            else:
                # Log errors from result.errors list
                for error_msg in result.errors:
                    self.logger.error(
                        f"Error processing item {item.key} "
                        f'("{item.title}"): {error_msg}'
                    )

                tag_result = self._apply_processing_tags(
                    item.key, success=False
                )
                if tag_result.has_failures:
                    self.logger.warning(
                        f"Some tag operations failed for item {item.key}"
                    )

            # Optional disk storage
            if self.processing_config.save_to_disk:
                try:
                    base_dir = Path(self.storage_config.base_dir)
                    safe_key = item.key.replace("/", "_")
                    item_dir_path = str(base_dir / safe_key)

                    # Collect tree structures for this item's documents
                    item_trees: dict[str, DocumentTree] = {}
                    if result.item_key in self.uploaded_docs_map:
                        for uploaded_doc in self.uploaded_docs_map[result.item_key]:
                            if (
                                uploaded_doc.attachment_key is not None
                                and uploaded_doc.doc_id in self._tree_structures
                            ):
                                item_trees[uploaded_doc.attachment_key] = (
                                    self._tree_structures[uploaded_doc.doc_id]
                                )

                    # Merge result.tree_structures into item_trees (prefer
                    # result.tree_structures as fallback)
                    if result.tree_structures:
                        for attachment_key, tree in result.tree_structures.items():
                            if attachment_key not in item_trees:
                                item_trees[attachment_key] = tree

                    self.logger.debug(
                        f"Prepared {len(item_trees)} trees for item {item.key}"
                    )
                    self._save_to_disk(item, result, item_trees)
                    log_disk_save(self.logger, item_dir_path)
                except Exception as e:
                    self.logger.warning(
                        f"Failed to save to disk for item {item.key}: {e}"
                    )

        # ========================================================================
        # Summary Aggregation
        # ========================================================================
        successful_items = sum(1 for r in results if r.success)
        failed_items = sum(1 for r in results if not r.success)
        total_pdfs = sum(r.pdfs_processed for r in results)
        total_pages = sum(r.pages_extracted for r in results)
        total_notes = sum(r.notes_created for r in results)
        total_time = time.time() - start_time

        summary = {
            "total_items": len(items),
            "successful_items": successful_items,
            "failed_items": failed_items,
            "skipped_items": discovery_stats.excluded_count,
            "matched_items": discovery_stats.matched_count,
            "excluded_by_rule": discovery_stats.excluded_by_rule,
            "total_pdfs_processed": total_pdfs,
            "total_pages_extracted": total_pages,
            "total_notes_created": total_notes,
            "total_time": total_time,
            "results": results,
        }

        # Add download metrics if download was enabled
        if self.download_config.enabled:
            summary["total_pdfs_downloaded"] = download_summary.get("downloaded", 0)
            summary["total_pdfs_skipped"] = download_summary.get("skipped", 0)
            summary["total_pdfs_failed"] = download_summary.get("failed", 0)

        # Add tag-adding metrics
        if self.tag_adding_config.enabled:
            tag_adding_failed_count = sum(
                1 for r in tag_adding_results_ocr if r.tags_failed
            )
            summary["tag_adding_results"] = tag_adding_results_ocr
            summary["tag_adding_failed"] = tag_adding_failed_count
            summary["tag_adding_matched"] = len(tag_adding_results_ocr)
            summary["tag_adding_succeeded"] = sum(
                1 for r in tag_adding_results_ocr if not r.tags_failed
            )
            summary["tag_adding_eligible"] = sum(
                1 for r in results if r.success
            )
            summary["tag_adding_no_key"] = tag_adding_no_key_total
            summary["tag_adding_processed"] = tag_adding_processed
        else:
            summary["tag_adding_results"] = []
            summary["tag_adding_failed"] = 0
            summary["tag_adding_matched"] = 0
            summary["tag_adding_succeeded"] = 0
            summary["tag_adding_eligible"] = 0
            summary["tag_adding_no_key"] = 0
            summary["tag_adding_processed"] = 0

        # Log completion
        log_completion(self.logger)

        # Log summary statistics
        self.logger.info("=" * 80)
        self.logger.info(
            f"Pipeline completed in {total_time:.1f}s: "
            f"{summary['total_items']} items processed, "
            f"{summary['successful_items']} successful, "
            f"{summary['failed_items']} failed"
        )
        self.logger.info(
            f"Summary: {summary['total_pdfs_processed']} PDFs processed, "
            f"{summary['total_pages_extracted']} pages extracted, "
            f"{summary['total_notes_created']} notes created"
        )
        if self.download_config.enabled:
            self.logger.info(
                f"Download: {summary.get('total_pdfs_downloaded', 0)} downloaded, "
                f"{summary.get('total_pdfs_skipped', 0)} skipped, "
                f"{summary.get('total_pdfs_failed', 0)} failed"
            )
        self.logger.info("=" * 80)

        return summary
