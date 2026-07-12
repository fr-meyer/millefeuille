"""
ItemProcessor orchestrates the processing of a single Zotero item through the
complete pipeline.

This module provides the ItemProcessor class which handles the end-to-end
processing of a single Zotero item: downloading PDFs → uploading to OCR
provider → OCR processing → formatting notes → creating notes in batches. The
processor handles multiple PDF attachments per item by processing each PDF
independently and aggregating results.

The processor implements per-PDF error isolation (allows partial success when
one PDF fails but others succeed) and all-or-nothing batch failure handling
(prevents partial note creation for a single PDF to maintain data consistency).

Example usage:
    >>> from millefeuille.clients.zotero_client import ZoteroClient
    >>> from millefeuille.clients.ocr_client import OCRClient
    >>> from millefeuille.domain.config import ProcessingConfig
    >>> from millefeuille.orchestration.processor import ItemProcessor
    >>>
    >>> processor = ItemProcessor(zotero_client, ocr_client, processing_config)
    >>> item = DiscoveredItem(
    ...     key='ITEM_KEY_123',
    ...     title='My Document',
    ...     tags=['millefeuille'],
    ...     attachments=[AttachmentInfo(key='ATTACH_KEY_1', filename='document.pdf')],
    ...     citation_key=None,
    ...     paper_metadata=PaperMetadata(),
    ... )
    >>> result = processor.process_item(item, ocr_results={})
    >>> print(f"Success: {result.success}, Notes: {result.notes_created}")
"""

import logging
import time

from millefeuille.clients.exceptions import (
    ZoteroClientError,
)
from millefeuille.clients.ocr_client import OCRClient
from millefeuille.clients.zotero_client import ZoteroClient
from millefeuille.domain.config import ProcessingConfig
from millefeuille.domain.models import (
    DiscoveredItem,
    DocumentTree,
    NotePayload,
    PageContent,
    ProcessingResult,
)
from millefeuille.domain.note_formatter import NoteFormatter
from millefeuille.domain.tree_processor import TreeStructureProcessor
from millefeuille.utils.logging import (
    log_error,
    log_note_creation_success,
)


class ItemProcessor:
    """Orchestrates the processing of a single Zotero item through the
    complete pipeline.

    This class handles the end-to-end processing of a single Zotero item, including:
    - Downloading PDF attachments from Zotero
    - Uploading PDFs to OCR provider service
    - Processing PDFs through OCR to extract page content
    - Formatting page content into Zotero note payloads
    - Creating notes in Zotero in batches (max 50 notes per batch)

    The processor implements per-PDF error isolation, allowing partial success when
    one PDF fails but others succeed. For each PDF, it implements all-or-nothing batch
    failure handling to prevent partial note creation and maintain data consistency.

    Attributes:
        zotero_client: Client for interacting with Zotero API.
        ocr_client: Client for interacting with OCR API.
        config: Processing configuration (batch size, skip empty pages, etc.).
        logger: Logger instance for this processor.
        tree_processor: Optional TreeStructureProcessor for document tree extraction.

    Example:
        >>> processor = ItemProcessor(
        ...     zotero_client, ocr_client, processing_config
        ... )
        >>> result = processor.process_item(item_dict)
        >>> if result.success:
        ...     print(
        ...         f"Created {result.notes_created} notes from "
        ...         f"{result.pages_extracted} pages"
        ...     )
    """

    def __init__(
        self,
        zotero_client: ZoteroClient,
        ocr_client: OCRClient | None,
        config: ProcessingConfig,
        tree_processor: TreeStructureProcessor | None = None,
    ) -> None:
        """Initialize the ItemProcessor with dependencies.

        Args:
            zotero_client: Client for interacting with Zotero API.
            ocr_client: OCR client when the pipeline runs OCR; may be None when
                OCR is disabled (not used by ``process_item``).
            config: Processing configuration containing batch_size and skip_empty_pages.
            tree_processor: Optional TreeStructureProcessor for extracting
                document tree structures.
                If None, tree extraction is skipped.

        Raises:
            ValueError: If batch_size exceeds 50 (Zotero API limit).
        """
        self.zotero_client = zotero_client
        self.ocr_client = ocr_client
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tree_processor = tree_processor

        # Validate batch size doesn't exceed Zotero API limit
        if config.batch_size > 50:
            raise ValueError(
                f"batch_size ({config.batch_size}) must not exceed 50 "
                f"(Zotero API limit)"
            )

    def process_item(
        self,
        item: DiscoveredItem,
        ocr_results: dict[str, list[PageContent]],
        pre_extracted_trees: dict[str, DocumentTree] | None = None,
    ) -> ProcessingResult:
        """Process a single Zotero item through the complete pipeline.

        This method orchestrates the processing of a single Zotero item by:
        1. Validating that the item has PDF attachments
        2. Processing each PDF attachment independently using pre-fetched OCR results
        3. Aggregating results (pages, notes, errors) across all PDFs
        4. Returning a ProcessingResult with complete metrics

        The method implements per-PDF error isolation: if one PDF fails, processing
        continues for remaining PDFs. The item is only marked as successful if ALL
        PDFs process successfully.

        Args:
            item: DiscoveredItem containing typed item metadata including key,
                title, tags, and attachments (list of AttachmentInfo).
            ocr_results: Required dictionary mapping attachment_key →
                List[PageContent]. Contains pre-fetched OCR results for all
                attachments. Keys must match attachment keys from the item's
                attachments.
            pre_extracted_trees: Optional dictionary mapping attachment_key →
                DocumentTree. If provided, uses pre-extracted tree structures
                instead of extracting them. If None, tree processing is skipped
                when no tree is available.

        Returns:
            ProcessingResult object containing:
                - item_key: Zotero item key
                - item_title: Item title
                - success: True if all PDFs processed successfully, False otherwise
                - pdfs_processed: Number of PDFs processed
                - pages_extracted: Total pages extracted across all PDFs
                - notes_created: Total notes created in Zotero
                - errors: List of error messages (empty if successful)
                - processing_time: Processing duration in seconds

        Raises:
            No exceptions are raised. All errors are captured and returned in the
            ProcessingResult.errors list.
        """
        start_time = time.time()
        item_key = item.key
        item_title = item.title

        # Initialize tracking variables
        total_pages = 0
        total_notes = 0
        errors: list[str] = []
        pdfs_processed = 0
        page_contents: dict[str, list[PageContent]] = {}
        pdf_trees: dict[str, DocumentTree] = {}

        # Validate item has PDF attachments
        attachments = item.attachments
        if not attachments:
            processing_time = time.time() - start_time
            return ProcessingResult(
                item_key=item_key,
                item_title=item_title,
                success=False,
                pdfs_processed=0,
                pages_extracted=0,
                notes_created=0,
                errors=["No PDF attachments found"],
                processing_time=processing_time,
            )

        self.logger.info(f"Processing item {item_key} with pre-fetched OCR results")

        # Process each PDF attachment using pre-fetched results
        total_pdfs = len(attachments)
        for pdf_index, attachment in enumerate(attachments, start=1):
            attachment_key = attachment.key
            filename = attachment.filename

            # Validate attachment has required fields
            if not attachment_key or not filename:
                self.logger.warning("Skipping attachment with missing key or filename")
                continue

            # Filter non-PDF attachments
            is_pdf = attachment.content_type == "application/pdf"
            has_pdf_suffix = filename.lower().endswith(".pdf")
            if not is_pdf and not has_pdf_suffix:
                self.logger.debug(
                    "Skipping non-PDF attachment "
                    "(no pdf content_type or .pdf suffix): "
                    f"{filename}"
                )
                continue

            try:
                # Look up OCR results for this attachment using attachment_key
                if attachment_key not in ocr_results:
                    self.logger.warning(
                        f"No OCR results found for attachment {attachment_key} "
                        f"(filename: {filename})"
                    )
                    errors.append(
                        f"No OCR results found for attachment {attachment_key} "
                        f"(filename: {filename})"
                    )
                    continue

                page_contents_list = ocr_results[attachment_key]

                # Guard against empty page contents
                if not page_contents_list:
                    self.logger.debug(
                        f"No page contents found for attachment {attachment_key} "
                        f"(filename: {filename})"
                    )
                    continue

                # Use pre-extracted tree from pipeline if available (no fallback
                # extraction). The pipeline handles provider-specific tree
                # extraction routing (PageIndex vs Mistral). Processor trusts
                # pipeline's tree extraction and doesn't attempt re-extraction.
                # Extract tree from pre_extracted_trees if available
                pre_extracted_tree = (
                    pre_extracted_trees.get(attachment_key)
                    if pre_extracted_trees
                    else None
                )

                # Process using pre-fetched results (skip download/upload/OCR)
                pages_extracted, notes_created, pdf_errors, pdf_page_contents, tree = (
                    self._process_with_prefetched_results(
                        item_key,
                        attachment_key,
                        filename,
                        item_title,
                        pdf_index,
                        total_pdfs,
                        page_contents_list,
                        pre_extracted_tree,
                    )
                )

                # Store page contents for disk persistence
                # (use attachment_key as dict key)
                if pdf_page_contents:
                    page_contents[attachment_key] = pdf_page_contents

                # Store tree structure for disk persistence
                # (use attachment_key as dict key)
                if tree:
                    pdf_trees[attachment_key] = tree

                total_pages += pages_extracted
                total_notes += notes_created
                errors.extend(pdf_errors)

                if not pdf_errors:
                    pdfs_processed += 1
                    self.logger.info(
                        f"Completed PDF {filename}: {pages_extracted} pages, "
                        f"{notes_created} notes created"
                    )
                else:
                    self.logger.warning(
                        f"PDF {filename} completed with errors: "
                        f"{len(pdf_errors)} error(s)"
                    )

            except Exception as e:
                # Catch any unexpected exceptions during PDF processing
                context = {
                    "item_key": item_key,
                    "item_title": item_title,
                    "step": "PDF Processing",
                    "attachment": filename,
                }
                log_error(self.logger, e, context)
                errors.append(f"Unexpected error processing PDF {filename}: {str(e)}")

        # Calculate processing time
        processing_time = time.time() - start_time

        # Determine overall success (all PDFs must succeed)
        success = len(errors) == 0

        return ProcessingResult(
            item_key=item_key,
            item_title=item_title,
            success=success,
            pdfs_processed=pdfs_processed,
            pages_extracted=total_pages,
            notes_created=total_notes,
            errors=errors,
            processing_time=processing_time,
            page_contents=page_contents if page_contents else None,
            tree_structures=pdf_trees if pdf_trees else None,
        )

    def _process_with_prefetched_results(
        self,
        item_key: str,
        attachment_key: str,
        filename: str,
        item_title: str,
        pdf_number: int = 1,
        total_pdfs: int = 1,
        page_contents: list[PageContent] | None = None,
        pre_extracted_tree: DocumentTree | None = None,
    ) -> tuple[int, int, list[str], list[PageContent], DocumentTree | None]:
        """Process a single PDF using pre-fetched OCR results.

        This helper method handles the processing of a single PDF when OCR results
        are already available from batch processing. It skips download/upload/OCR
        phases and proceeds directly to note formatting and creation.

        The method implements all-or-nothing batch failure handling: if any batch
        fails during note creation, no notes are created for this PDF (to maintain
        data consistency).

        Args:
            item_key: Zotero item key that owns the attachment.
            attachment_key: Zotero attachment key for the PDF file.
            filename: PDF filename for logging and note titles.
            item_title: Item title for error context.
            pdf_number: Current PDF number (1-indexed) for progress logging.
            total_pdfs: Total number of PDFs to process for progress logging.
            page_contents: Pre-fetched list of PageContent objects from OCR.
            pre_extracted_tree: Optional pre-extracted DocumentTree. If
                provided, uses this tree directly. If None and tree_processor is
                available, logs a debug message and skips tree processing.

        Returns:
            Tuple containing:
                - pages_extracted: Number of pages extracted
                - notes_created: Number of notes created (0 on failure)
                - errors: List of error messages (empty list on success)
                - page_contents: List of PageContent objects
                - tree: Optional DocumentTree object (None if not provided or
                    unavailable)
        """
        errors: list[str] = []
        if page_contents is None:
            page_contents = []
        pages_extracted = len(page_contents)
        notes_created = 0

        try:
            # Step 3: Use pre-extracted tree if available, otherwise skip
            # tree processing
            tree: DocumentTree | None = None
            if pre_extracted_tree is not None:
                tree = pre_extracted_tree
            elif self.tree_processor is not None:
                self.logger.debug(
                    f"No pre-extracted tree provided for {filename}, skipping "
                    f"tree processing"
                )
                tree = None

            # Step 4.1: Format Main Notes
            main_notes = NoteFormatter.format_main_notes(
                pages=page_contents,
                parent_item_key=item_key,
                pdf_filename=filename,
                auto_split=self.config.auto_split_oversized_notes,
                size_threshold=self.config.note_size_threshold,
                extraction_mode=self.config.extraction_mode,
                skip_empty_pages=self.config.skip_empty_pages,
            )

            # Step 4.2: Check for Empty Content
            if len(main_notes) == 0 and pages_extracted > 0:
                self.logger.warning(
                    f"No content extracted from {filename} (all pages empty)"
                )
                # Not treated as error, just skip note creation
                # Return page_contents even if empty (for disk persistence)
                return (pages_extracted, 0, [], page_contents, tree)

            # Step 4.3: Create Main Notes in Batches
            self.logger.info(
                f"Creating {len(main_notes)} main page notes for {filename}"
            )
            try:
                main_note_keys = self._create_notes_in_batches(
                    main_notes, item_key, filename
                )

                notes_created = len(main_note_keys)

                self.logger.info(
                    f"Successfully created {notes_created} total notes for {filename}"
                )
                log_note_creation_success(self.logger, notes_created)

            except ZoteroClientError:
                # Error already logged by helper
                errors.append(f"Failed to create main page notes for {filename}")
                return (pages_extracted, 0, errors, page_contents, tree)

        except Exception as e:
            # Catch any unexpected exceptions
            context = {
                "item_key": item_key,
                "item_title": item_title,
                "step": "PDF Processing",
                "attachment": filename,
            }
            log_error(self.logger, e, context)
            errors.append(f"Unexpected error processing PDF {filename}: {str(e)}")
            return (pages_extracted, 0, errors, page_contents, None)

        # Success case: return counts, empty errors list, page contents, and tree
        return (pages_extracted, notes_created, [], page_contents, tree)

    def _create_notes_in_batches(
        self, notes: list[NotePayload], item_key: str, pdf_filename: str
    ) -> list[str]:
        """Create notes in batches with rollback on failure.

        This helper method splits notes into batches of configurable size
        (default 50), creates each batch via ZoteroClient, and collects note
        keys. If any batch fails, all previously created notes are rolled back
        (deleted) to maintain all-or-nothing semantics.

        Args:
            notes: List of NotePayload objects to create in Zotero.
            item_key: Zotero item key that will be the parent of all notes.
            pdf_filename: PDF filename for error context and logging.

        Returns:
            List of note keys (strings) for all successfully created notes.

        Raises:
            ZoteroClientError: If any batch fails. All previously created notes are
                rolled back before the exception is re-raised.
        """
        created_note_keys: list[str] = []
        batch_size = self.config.batch_size

        try:
            # Split notes into batches
            for i in range(0, len(notes), batch_size):
                batch = notes[i : i + batch_size]
                batch_number = (i // batch_size) + 1

                try:
                    # Create batch and collect note keys for potential rollback
                    batch_note_keys = self.zotero_client.create_notes_batch(
                        batch, item_key
                    )
                    created_note_keys.extend(batch_note_keys)
                    self.logger.debug(
                        f"Created batch {batch_number} ({len(batch)} notes) "
                        f"for PDF {pdf_filename}"
                    )
                except ZoteroClientError as e:
                    # Batch failure triggers rollback of all previously created notes
                    context = {
                        "item_key": item_key,
                        "step": "Note Creation",
                        "batch_number": batch_number,
                        "attachment": pdf_filename,
                    }
                    log_error(self.logger, e, context)

                    # Rollback: delete all previously created notes
                    if created_note_keys:
                        self.logger.warning(
                            f"Rolling back {len(created_note_keys)} previously "
                            f"created notes for PDF {pdf_filename} due to "
                            f"batch {batch_number} failure"
                        )
                        try:
                            self.zotero_client.delete_notes(created_note_keys)
                            self.logger.info(
                                f"Successfully rolled back notes for PDF {pdf_filename}"
                            )
                        except Exception as rollback_error:
                            # Log rollback failure but don't mask the original error
                            self.logger.error(
                                f"Failed to rollback notes for PDF {pdf_filename}: "
                                f"{rollback_error}",
                                exc_info=True,
                            )

                    # Re-raise exception to propagate failure
                    raise

        except ZoteroClientError:
            # Re-raise to allow caller to handle cleanup
            raise

        return created_note_keys
