"""
Abstract base class for OCR client implementations.

This module defines the abstract interface that all OCR provider implementations
must follow. The interface follows an upload-then-process workflow pattern where
PDFs are first uploaded to the provider's storage, then processed for OCR extraction.

Example workflow:
    # 1. doc_id = client.upload_pdf(pdf_bytes, "document.pdf")
    # 2. pages = client.process_pdf(doc_id)
    # 3. client.cleanup_file(doc_id)

Implementations must handle provider-specific details internally (e.g., storing
signed URLs as instance variables) and expose a consistent interface through
this abstract base class.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum

from ..domain.models import PageContent, UploadedDocument


class OCRProvider(Enum):
    """Enumeration for type-safe OCR provider identification.

    This enum provides type-safe provider identification without relying on
    string comparisons or isinstance() checks. Each enum member corresponds
    to a specific OCR provider implementation.

    Values:
        PAGEINDEX: PageIndex OCR provider
        MISTRAL: Mistral AI OCR provider
    """

    PAGEINDEX = "pageindex"
    MISTRAL = "mistral"


class OCRClient(ABC):
    """Abstract base class for all OCR provider implementations.

    This class defines the interface that all OCR clients must implement,
    ensuring consistent behavior across different OCR providers (Mistral,
    PageIndex, etc.). The interface follows an upload-then-process workflow
    pattern where documents are first uploaded to provider storage, then
    processed for OCR extraction.

    Implementations should store any provider-specific data (like signed URLs)
    internally and use document IDs as the primary interface for subsequent
    operations.

    Note:
        The upload_pdf() method signature has been changed from returning a tuple
        (file_id, signed_url) to returning a single document ID string. Implementations
        should store additional data like signed URLs as instance variables.
    """

    @property
    @abstractmethod
    def provider(self) -> OCRProvider:
        """Returns the OCR provider type for this client instance.

        This property enables runtime provider detection without string comparisons
        or isinstance checks. Each concrete implementation must return its corresponding
        OCRProvider enum member.

        Returns:
            OCRProvider enum member identifying the provider (PAGEINDEX or MISTRAL).

        Usage:
            Enables type-safe provider identification:
            >>> if client.provider == OCRProvider.PAGEINDEX:
            ...     # PageIndex-specific logic
        """
        pass

    @abstractmethod
    def upload_pdf(self, pdf_bytes: bytes, filename: str) -> str:
        """Upload PDF and return document ID for processing.

        This method uploads a PDF file to the OCR provider's storage and returns
        a document identifier that can be used for subsequent processing operations.
        Implementations should store any additional data (like signed URLs) internally
        as instance variables.

        Args:
            pdf_bytes: PDF file content as bytes.
            filename: Name of the PDF file (used for identification).

        Returns:
            Single string representing the document/file ID for use in
            subsequent operations.

        Raises:
            OCRClientError: If upload fails (provider-specific subclasses).
        """
        pass

    @abstractmethod
    def upload_pdfs_batch(
        self, pdfs: list[tuple[bytes, str, str, str]]
    ) -> list[UploadedDocument]:
        """Upload multiple PDFs in batch and return list of uploaded documents.

        This method uploads multiple PDF files in a single batch operation, which
        is more efficient than calling upload_pdf() multiple times. The method
        continues uploading remaining PDFs when one fails, collecting and logging
        failures instead of raising immediately. Only successfully uploaded documents
        are returned in the result list.

        Args:
            pdfs: List of 4-tuples containing (pdf_bytes, filename, item_key,
                attachment_key) for each PDF to upload. The 4-tuple structure is:
                - First element: pdf_bytes (bytes) - PDF file content
                - Second element: filename (str) - Name of the PDF file
                - Third element: item_key (str) - Zotero item key for tracking
                - Fourth element: attachment_key (str) - Zotero attachment key
                    for tracking

        Returns:
            List of UploadedDocument objects for successfully uploaded PDFs. Each
            UploadedDocument contains doc_id, filename, upload_time, item_key, and
            attachment_key. The item_key and attachment_key are set from the 4-tuple
            metadata. Failed uploads are logged but not included in the result.

        Note:
            For providers that don't support batch operations, implementations can
            fall back to sequential upload_pdf() calls. Errors are isolated per PDF
            to allow partial success.
        """
        pass

    @abstractmethod
    def process_pdf(self, doc_id: str) -> list[PageContent]:
        """Process uploaded PDF and extract page content.

        This method processes a previously uploaded PDF and extracts structured
        content from each page. Implementations should use internally stored data
        (e.g., signed URLs) as needed to access the uploaded file.

        Args:
            doc_id: Document identifier returned from upload_pdf() or
                upload_pdfs_batch().

        Returns:
            List of PageContent objects, one per page in the PDF. Pages are 1-indexed.

        Raises:
            OCRClientError: If OCR processing fails (provider-specific subclasses).
        """
        pass

    @abstractmethod
    def poll_ocr_results_batch(
        self,
        doc_ids: list[str],
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, list[PageContent]]:
        """Poll OCR results for multiple documents with exponential backoff.

        This method polls the OCR provider for results of multiple documents that
        were previously uploaded. It implements retry logic with exponential backoff
        to handle asynchronous processing workflows. Successful results are preserved
        even when some documents fail, with errors logged separately.

        Args:
            doc_ids: List of document identifiers to poll for results.
            progress_callback: Optional callback function that reports
                per-document status transitions. Called with (doc_id, status)
                where status is one of: "processing" (document begins processing),
                "completed" (document processing completed successfully), "failed"
                (document processing failed permanently). Defaults to None
                (no progress reporting).

        Returns:
            Dictionary mapping doc_id to list of PageContent objects for that document.
            Only successfully processed documents are included in the dictionary.
            Failed documents are logged but not included in the result, allowing
            partial success.

        Note:
            For providers with synchronous processing, implementations can call
            process_pdf() for each document ID. Errors are isolated per document
            to allow partial success.
        """
        pass

    @abstractmethod
    def cleanup_file(self, doc_id: str) -> None:
        """Delete uploaded file from provider storage.

        This method removes a previously uploaded file from the OCR provider's
        storage. This is optional but recommended to avoid accumulating unused files.

        Args:
            doc_id: Document identifier to delete (from upload_pdf() or
                upload_pdfs_batch()).

        Note:
            This method should not raise exceptions, only log warnings on failure.
            File cleanup is non-critical and should not fail the main processing flow.
        """
        pass
