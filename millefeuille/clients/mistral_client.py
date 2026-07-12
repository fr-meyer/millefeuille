"""Mistral AI OCR client implementation.

This module provides a high-level interface for interacting with the Mistral AI
OCR API, handling file uploads, OCR processing, and result parsing into domain models.
"""

from collections.abc import Callable
import logging
import re
import time
from typing import Literal, cast

from mistralai import Mistral, MistralError

from ..domain.config import MistralOCRConfig
from ..domain.models import PageContent, UploadedDocument
from .exceptions import (
    MistralAPIError,
    MistralClientError,
    MistralOCRError,
    MistralUploadError,
)
from .ocr_client import OCRClient, OCRProvider

logger = logging.getLogger(__name__)


class MistralClient(OCRClient):
    """Client for interacting with Mistral AI OCR API.

    This client encapsulates all interactions with the Mistral AI OCR service,
    including file uploads, OCR processing, and cleanup operations. It follows
    the upload-then-process pattern for handling large PDF files efficiently.
    Signed URLs are managed internally and not exposed in the public interface.

    Example:
        >>> config = MistralOCRConfig(api_key="your-key", model="mistral-ocr-latest")
        >>> client = MistralClient(config)
        >>> file_id = client.upload_pdf(pdf_bytes, "document.pdf")
        >>> pages = client.process_pdf(file_id)
        >>> client.cleanup_file(file_id)
    """

    # Threshold for determining if a page is empty (in characters)
    EMPTY_PAGE_THRESHOLD = 50

    def __init__(self, config: MistralOCRConfig) -> None:
        """Initialize the Mistral client.

        Args:
            config: Mistral configuration containing API key and model settings.

        Raises:
            MistralAPIError: If client initialization fails (e.g., invalid API key).
        """
        try:
            self.config = config
            self.client = Mistral(api_key=config.api_key)
            self._uploaded_files: dict[str, str] = {}
            logger.info("MistralClient initialized successfully")
        except Exception as e:
            error_msg = f"Failed to initialize Mistral client: {str(e)}"
            logger.error(error_msg)
            raise MistralAPIError(error_msg, original_exception=e) from e

    @property
    def provider(self) -> OCRProvider:
        """Returns OCRProvider.MISTRAL to identify this client as a Mistral provider."""
        return OCRProvider.MISTRAL

    def upload_pdf(self, pdf_bytes: bytes, filename: str) -> str:
        """Upload a PDF file to Mistral Files API and get a signed URL.

        This method uploads the PDF to Mistral's file storage and retrieves
        a signed URL that can be used for OCR processing. This approach is
        recommended for large files as it avoids base64 encoding overhead.
        The signed URL is stored internally and not exposed in the return value.

        Args:
            pdf_bytes: PDF file content as bytes.
            filename: Name of the PDF file (used for identification).

        Returns:
            Document identifier (file_id) for use in subsequent processing operations.
            The signed URL is stored internally for later lookup.

        Raises:
            MistralUploadError: If file upload fails or signed URL retrieval fails.
        """
        try:
            # Upload file to Mistral Files API
            logger.info(f"Uploading PDF file: {filename}")
            uploaded = self.client.files.upload(
                file={"file_name": filename, "content": pdf_bytes},
                purpose="ocr",
            )

            # Get signed URL for OCR processing
            signed = self.client.files.get_signed_url(file_id=uploaded.id)

            # Store signed URL internally for later lookup
            self._uploaded_files[uploaded.id] = signed.url

            logger.info(
                f"Successfully uploaded file: {filename} (file_id: {uploaded.id})"
            )
            return uploaded.id

        except MistralError as e:
            error_msg = f"Failed to upload PDF file '{filename}': {str(e)}"
            logger.error(error_msg)
            raise MistralUploadError(error_msg, original_exception=e) from e
        except Exception as e:
            error_msg = f"Unexpected error uploading PDF file '{filename}': {str(e)}"
            logger.error(error_msg)
            raise MistralUploadError(error_msg, original_exception=e) from e

    def upload_pdfs_batch(
        self, pdfs: list[tuple[bytes, str, str, str]]
    ) -> list[UploadedDocument]:
        """Upload multiple PDFs in batch and return list of uploaded documents.

        This method uploads multiple PDF files sequentially (Mistral doesn't support
        native batch uploads). The method continues uploading remaining PDFs when one
        fails, collecting and logging failures instead of raising immediately. Only
        successfully uploaded documents are returned in the result list.

        Args:
            pdfs: List of 4-tuples containing (pdf_bytes, filename, item_key,
                attachment_key) for each PDF to upload. The 4-tuple structure is:
                - First element: pdf_bytes (bytes) - PDF file content
                - Second element: filename (str) - Name of the PDF file
                - Third element: item_key (str) - Zotero item key for tracking
                - Fourth element: attachment_key (str) - Zotero attachment key
                    for tracking

        Returns:
            List of UploadedDocument objects for successfully uploaded PDFs.
            Each UploadedDocument contains doc_id, filename, upload_time, item_key, and
            attachment_key. Failed uploads are logged but not included in the result.
        """
        uploaded_docs = []
        errors = []
        total = len(pdfs)

        for index, (pdf_bytes, filename, item_key, attachment_key) in enumerate(
            pdfs, start=1
        ):
            try:
                upload_start_time = time.time()
                doc_id = self.upload_pdf(pdf_bytes, filename)

                # Create UploadedDocument object
                uploaded_doc = UploadedDocument(
                    doc_id=doc_id,
                    filename=filename,
                    upload_time=upload_start_time,
                    item_key=item_key,  # Set from 4-tuple metadata
                    attachment_key=attachment_key,  # Set from 4-tuple metadata
                )
                uploaded_docs.append(uploaded_doc)
                logger.info(f"Uploaded {filename} ({index}/{total})")

            except MistralClientError as e:
                # Log error but continue with remaining PDFs
                error_msg = f"Failed to upload {filename} in batch: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                continue

        success_count = len(uploaded_docs)
        failure_count = len(errors)

        if failure_count > 0:
            logger.warning(
                f"Batch upload completed with {failure_count} failure(s): "
                f"{success_count} succeeded, {failure_count} failed"
            )
        else:
            logger.info(f"Successfully uploaded {success_count} PDFs in batch")

        return uploaded_docs

    def _embed_images_in_markdown(self, markdown: str, images: list) -> str:
        """Embed images directly into markdown by replacing placeholders with
        base64 data URIs.

        This method follows the reference implementation approach where OCR
        placeholders like `![img-0.jpeg](img-0.jpeg)` are replaced with base64
        data URIs. This keeps images inline where they naturally appear in the
        document, resulting in cleaner self-contained markdown.

        The method handles both cases:
        - If image_base64 already starts with "data:", use it directly
        - Otherwise, create a proper data URI based on the image file extension

        Args:
            markdown: Markdown content with image placeholders.
            images: List of image objects from OCR response with `id` and
                `image_base64` attributes.

        Returns:
            Markdown string with images embedded as base64 data URIs inline
            where they appear.
        """
        if not images:
            return markdown

        for img in images:
            if not (
                hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64
            ):
                continue

            # OCR placeholders look like: ![img-0.jpeg](img-0.jpeg)
            placeholder = f"![{img.id}]({img.id})"

            # Use base64 string directly if it's already a data URI
            if isinstance(img.image_base64, str) and img.image_base64.startswith(
                "data:"
            ):
                data_uri = img.image_base64
            else:
                # Create proper data URI based on file extension
                img_id_lower = img.id.lower()
                if img_id_lower.endswith((".png",)):
                    mime_type = "image/png"
                elif img_id_lower.endswith((".jpg", ".jpeg")):
                    mime_type = "image/jpeg"
                elif img_id_lower.endswith((".gif",)):
                    mime_type = "image/gif"
                elif img_id_lower.endswith((".webp",)):
                    mime_type = "image/webp"
                else:
                    mime_type = "image/png"  # Default fallback

                data_uri = f"data:{mime_type};base64,{img.image_base64}"

            # Replace placeholder with data URI
            markdown = markdown.replace(placeholder, f"![{img.id}]({data_uri})")

        return markdown

    def _extract_table_content(self, table_obj) -> str:
        """Extract table content from various table object types.

        This helper method handles different table object structures:
        - String objects: return directly
        - Objects with .content attribute: return table.content
        - Other objects: convert to string representation

        Args:
            table_obj: Table object that may be a string, object with .content,
                or other type.

        Returns:
            Table content as a string (markdown or HTML format).
        """
        if isinstance(table_obj, str):
            return table_obj
        elif hasattr(table_obj, "content"):
            return table_obj.content
        else:
            # Fallback: convert to string representation
            return str(table_obj)

    def _embed_tables_in_markdown(self, markdown: str, tables: list) -> str:
        """Embed tables directly into markdown by replacing placeholders with
        table content.

        This method follows the same approach as image embedding where OCR
        placeholders like `[tbl-3.md](tbl-3.md)` or `[tbl-3.html](tbl-3.html)`
        are replaced with actual table content (markdown or HTML format). This
        keeps tables inline where they naturally appear in the document.

        The method handles two matching strategies:
        1. ID-based matching: If table objects have an `id` attribute, match by ID
        2. Index-based matching: Extract table number from placeholder and use as index

        Supports both markdown and HTML table formats based on the
        table_format configuration.

        Args:
            markdown: Markdown content with table placeholders.
            tables: List of table objects from OCR response.

        Returns:
            Markdown string with tables embedded inline where they appear.
        """
        if not tables:
            return markdown

        # Pattern to match table placeholders: [tbl-3.md](tbl-3.md),
        # [tbl-3.html](tbl-3.html), or [tbl-3](tbl-3)
        # Captures the table number in group 1 and optional extension in group 2
        placeholder_pattern = (
            r"\[tbl-(\d+)(?:\.(md|html))?\]\(tbl-\d+(?:\.(?:md|html))?\)"
        )

        # Find all placeholders in markdown
        matches = list(re.finditer(placeholder_pattern, markdown))

        if not matches:
            # No placeholders found, return markdown as-is
            return markdown

        # Build a mapping of table identifiers to table content
        # Try ID-based matching first, then fallback to index-based
        table_map = {}
        for idx, table_obj in enumerate(tables):
            table_content = self._extract_table_content(table_obj)

            # Try to get table ID if available
            if hasattr(table_obj, "id") and table_obj.id:
                table_id = str(table_obj.id)
                # Normalize ID (remove .md or .html extension if present for matching)
                table_id_normalized = table_id.replace(".md", "").replace(".html", "")
                table_map[table_id] = table_content
                table_map[table_id_normalized] = table_content
                # Also map with both .md and .html extensions if not already present
                if not table_id.endswith(".md") and not table_id.endswith(".html"):
                    table_map[f"{table_id}.md"] = table_content
                    table_map[f"{table_id}.html"] = table_content

            # Also map by index (0-based and 1-based)
            table_map[str(idx)] = table_content
            table_map[str(idx + 1)] = table_content

        # Replace placeholders in reverse order to maintain positions
        for match in reversed(matches):
            placeholder = match.group(0)
            table_num = match.group(1)  # Extracted table number
            extension = (
                match.group(2) if len(match.groups()) > 1 else None
            )  # Optional extension (.md or .html)

            # Try to find matching table content
            table_content = None

            # Try matching by ID first (with and without extension)
            # Check both .md and .html extensions, plus no extension
            for ext in [extension, "md", "html", None]:
                table_id_key = f"tbl-{table_num}.{ext}" if ext else f"tbl-{table_num}"

                if table_id_key in table_map:
                    table_content = table_map[table_id_key]
                    break

            # Fallback to index-based matching (table numbers are typically 1-based)
            if table_content is None:
                # Try 0-based index
                if table_num in table_map:
                    table_content = table_map[table_num]
                # Try 1-based index (subtract 1 to convert to 0-based)
                elif str(int(table_num) - 1) in table_map:
                    table_content = table_map[str(int(table_num) - 1)]

            # Replace placeholder with table content if found
            if table_content:
                markdown = (
                    markdown[: match.start()] + table_content + markdown[match.end() :]
                )
            else:
                # Log warning if placeholder found but no matching table
                logger.warning(
                    f"Table placeholder {placeholder} found but no matching "
                    f"table content"
                )

        return markdown

    def process_pdf(self, doc_id: str) -> list[PageContent]:
        """Process a PDF through Mistral OCR API and extract page content.

        This method sends the signed URL (retrieved internally from doc_id) to
        the Mistral OCR API and processes the response into PageContent objects.
        Each page is analyzed for content and structured data (images, tables)
        is extracted. Images are embedded directly into the markdown if
        embed_images_in_markdown is True (default: False), otherwise placeholders
        remain in markdown. Tables are always embedded.

        Args:
            doc_id: Document identifier returned from upload_pdf() method. The signed
                URL is looked up internally from stored mappings.

        Returns:
            List of PageContent objects, one for each page in the PDF. Pages are
            1-indexed and include markdown content. Images are embedded inline if
            embed_images_in_markdown is True, otherwise placeholders remain. Tables
            are always embedded inline. Content flags and separate image/table lists
            are included for backward compatibility.

        Raises:
            MistralOCRError: If OCR processing fails, response parsing fails, or
                document ID is not found in uploaded files.
        """
        try:
            # Retrieve signed URL from internal storage
            if doc_id not in self._uploaded_files:
                error_msg = f"Document ID {doc_id} not found in uploaded files"
                logger.error(error_msg)
                raise MistralOCRError(error_msg)

            signed_url = self._uploaded_files[doc_id]

            logger.info(
                f"Processing PDF via OCR API "
                f"(model: {self.config.model}, doc_id: {doc_id})"
            )

            # Call OCR API with configuration from MistralOCRConfig
            # Cast table_format to Literal type expected by Mistral SDK
            table_format: Literal["markdown", "html"] | None = None
            if self.config.table_format in ("markdown", "html"):
                table_format = cast(
                    Literal["markdown", "html"], self.config.table_format
                )

            ocr_response = self.client.ocr.process(
                model=self.config.model,
                document={"type": "document_url", "document_url": signed_url},
                table_format=table_format,
                include_image_base64=self.config.include_image_base64,
                extract_header=self.config.extract_header,
                extract_footer=self.config.extract_footer,
            )

            # Parse pages into PageContent objects
            pages = []
            for idx, page in enumerate(ocr_response.pages):
                page_number = idx + 1  # 1-indexed page numbers
                markdown = page.markdown
                has_content = not self._is_empty_page(markdown)

                # Extract images if available
                images = []
                if hasattr(page, "images") and page.images:
                    # Keep full image objects for embedding (check attribute
                    # exists and is a non-empty string)
                    image_objects = [
                        img
                        for img in page.images
                        if hasattr(img, "image_base64")
                        and img.image_base64
                        and isinstance(img.image_base64, str)
                    ]

                    # Embed images directly into markdown only if configured to do so
                    if self.config.embed_images_in_markdown:
                        markdown = self._embed_images_in_markdown(
                            markdown, image_objects
                        )
                    else:
                        logger.debug(
                            f"Skipping image embedding for page {page_number} "
                            f"(embed_images_in_markdown=False, "
                            f"{len(image_objects)} images available)"
                        )

                    # Extract base64 strings for backward compatibility
                    # Filter out None values to ensure List[str] type
                    images = [
                        img.image_base64
                        for img in image_objects
                        if img.image_base64 is not None
                        and isinstance(img.image_base64, str)
                    ]

                # Extract tables if available and embed them directly in markdown
                tables = []
                if hasattr(page, "tables") and page.tables:
                    # Keep full table objects for embedding
                    table_objects = list(page.tables)

                    # Embed tables directly into markdown
                    # (like reference implementation)
                    markdown = self._embed_tables_in_markdown(markdown, table_objects)

                    # Extract table strings for backward compatibility
                    tables = [self._extract_table_content(tbl) for tbl in table_objects]

                # Recompute has_content after embedding images/tables
                # Image-only or table-only pages should not be marked as empty
                has_content = (
                    (not self._is_empty_page(markdown)) or bool(images) or bool(tables)
                )

                page_content = PageContent(
                    page_number=page_number,
                    markdown=markdown,
                    has_content=has_content,
                    images=images,
                    tables=tables,
                )
                pages.append(page_content)

            logger.info(f"Successfully processed PDF: {len(pages)} pages extracted")
            return pages

        except MistralError as e:
            error_msg = f"Failed to process PDF via OCR API: {str(e)}"
            logger.error(error_msg)
            raise MistralOCRError(error_msg, original_exception=e) from e
        except Exception as e:
            error_msg = f"Unexpected error processing PDF via OCR API: {str(e)}"
            logger.error(error_msg)
            raise MistralOCRError(error_msg, original_exception=e) from e

    def poll_ocr_results_batch(
        self,
        doc_ids: list[str],
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, list[PageContent]]:
        """Poll OCR results for multiple documents with exponential backoff.

        For Mistral's synchronous processing, this method processes each document
        sequentially with exponential backoff between requests to avoid rate limits.
        Backoff sequence: 2s → 4s → 8s → 16s → 30s (max). Successful results are
        preserved even when some documents fail, with errors logged separately.

        Args:
            doc_ids: List of document identifiers to process.
            progress_callback: Optional callback function that reports
                per-document status transitions. Called with (doc_id, status)
                where status is one of: "processing" (document begins
                processing), "completed" (document processing completed
                successfully), "failed" (document processing failed
                permanently). Defaults to None (no progress reporting).

        Returns:
            Dictionary mapping doc_id to list of PageContent objects for that document.
            Only successfully processed documents are included in the dictionary.
            Failed documents are logged but not included in the result, allowing
            partial success.
        """
        results: dict[str, list[PageContent]] = {}
        errors = []
        backoff_delays = [2, 4, 8, 16, 30]  # Exponential backoff sequence in seconds
        total = len(doc_ids)

        for idx, doc_id in enumerate(doc_ids):
            try:
                # Report that document begins processing
                if progress_callback:
                    progress_callback(doc_id, "processing")

                # Process document
                page_contents = self.process_pdf(doc_id)
                results[doc_id] = page_contents
                logger.info(
                    f"Processed document {doc_id} ({idx + 1}/{total}): "
                    f"{len(page_contents)} pages"
                )

                # Report that document completed successfully
                if progress_callback:
                    progress_callback(doc_id, "completed")

                # Apply exponential backoff between requests (except after last
                # document)
                if idx < len(doc_ids) - 1:
                    delay = backoff_delays[min(idx, len(backoff_delays) - 1)]
                    logger.debug(f"Applying {delay}s backoff before next document")
                    time.sleep(delay)

            except MistralClientError as e:
                # Report that document failed
                if progress_callback:
                    progress_callback(doc_id, "failed")

                # Log error but continue with remaining documents
                error_msg = f"Failed to process document {doc_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                # Error isolation: continue with remaining documents
                continue

        success_count = len(results)
        failure_count = len(errors)

        if failure_count > 0:
            logger.warning(
                f"Batch processing completed with {failure_count} failure(s): "
                f"{success_count} succeeded, {failure_count} failed"
            )
        else:
            logger.info(f"Successfully processed {success_count} documents in batch")

        return results

    def cleanup_file(self, doc_id: str) -> None:
        """Delete an uploaded file from Mistral Files API.

        This method removes the file from Mistral's storage after processing
        is complete. This is optional but recommended to avoid accumulating
        unused files in your Mistral account.

        Args:
            doc_id: Unique identifier for the file to delete (from upload_pdf).

        Note:
            This method logs errors but does not raise exceptions, as file
            cleanup is non-critical and should not fail the main processing flow.
        """
        try:
            self.client.files.delete(file_id=doc_id)
            logger.info(f"Successfully deleted file: {doc_id}")
        except Exception as e:
            # Log but don't raise - cleanup failures are non-critical
            logger.warning(f"Failed to delete file {doc_id}: {str(e)}")

    def _is_empty_page(self, markdown: str) -> bool:
        """Determine if a page contains meaningful content.

        This helper method checks if the markdown content from a page is
        effectively empty (e.g., blank pages, pages with only whitespace).

        Args:
            markdown: Markdown content extracted from the page.

        Returns:
            True if the page is considered empty (content length below threshold),
            False otherwise.
        """
        stripped = markdown.strip()
        return len(stripped) < self.EMPTY_PAGE_THRESHOLD
