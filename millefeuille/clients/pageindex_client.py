"""PageIndex OCR client implementation.

This module provides a high-level interface for interacting with the PageIndex
OCR API, handling file uploads, OCR processing, and result parsing into domain models.
"""

from collections import deque
from collections.abc import Callable
import json
import logging
import time
from typing import Any

import requests

# Optional SDK import at module level
try:
    from pageindex import PageIndexClient as PageIndexSDK
except ImportError:
    PageIndexSDK = None  # SDK not installed, will be checked at runtime

from ..domain.config import PageIndexOCRConfig
from ..domain.models import PageContent, UploadedDocument
from .exceptions import PageIndexAPIError, PageIndexOCRError, PageIndexUploadError
from .ocr_client import OCRClient, OCRProvider
from .temp_file_utils import temporary_pdf_file

logger = logging.getLogger(__name__)


class PageIndexClient(OCRClient):
    """Client for interacting with PageIndex OCR API.

    This client encapsulates all interactions with the PageIndex OCR service,
    including file uploads, OCR processing, and cleanup operations. It follows
    the upload-then-process pattern for handling large PDF files efficiently.

    Example:
        >>> config = PageIndexOCRConfig(
        ...     api_key="your-key", base_url="https://api.pageindex.ai"
        ... )
        >>> client = PageIndexClient(config)
        >>> doc_id = client.upload_pdf(pdf_bytes, "document.pdf")
        >>> pages = client.process_pdf(doc_id)
        >>> client.cleanup_file(doc_id)
    """

    # Threshold for determining if a page is empty (in characters)
    EMPTY_PAGE_THRESHOLD = 50

    # Default timeout for HTTP requests (in seconds)
    DEFAULT_TIMEOUT = 10

    def __init__(self, config: PageIndexOCRConfig) -> None:
        """Initialize the PageIndex client.

        Args:
            config: PageIndex configuration containing API key and base URL settings.

        Raises:
            PageIndexAPIError: If client initialization fails (e.g., invalid API key).
        """
        try:
            self.config = config
            self._uploaded_documents: dict[str, dict] = {}
            self._use_sdk: bool = config.use_sdk
            self._sdk_client: Any | None = None
            self._session: requests.Session | None = None

            # Conditional initialization based on use_sdk flag
            if config.use_sdk:
                # Check if SDK is available
                if PageIndexSDK is None:
                    error_msg = (
                        "PageIndex SDK is not installed. "
                        "Install it with: pip install pageindex. "
                        "Alternatively, set use_sdk=False to use HTTP API mode."
                    )
                    logger.error(error_msg)
                    raise PageIndexAPIError(error_msg)

                # Initialize PageIndex SDK client
                self._sdk_client = PageIndexSDK(api_key=config.api_key)
                # Mask API key for security (show only first 4 characters)
                masked_key = (
                    config.api_key[:4] + "****" if len(config.api_key) > 4 else "****"
                )
                logger.info(
                    f"PageIndexClient initialized successfully in SDK mode "
                    f"(api_key: {masked_key})"
                )
            else:
                # HTTP mode: Initialize requests session (existing behavior)
                self._session = requests.Session()

                # Set default headers
                self._session.headers.update(
                    {
                        "Authorization": f"Bearer {config.api_key}",
                    }
                )

                logger.info(
                    f"PageIndexClient initialized successfully in HTTP API mode "
                    f"(base_url: {config.base_url})"
                )
        except Exception as e:
            error_msg = f"Failed to initialize PageIndex client: {str(e)}"
            logger.error(error_msg)
            raise PageIndexAPIError(error_msg, original_exception=e) from e

    @property
    def provider(self) -> OCRProvider:
        """Returns OCRProvider.PAGEINDEX to identify this client as a
        PageIndex provider."""
        return OCRProvider.PAGEINDEX

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Centralized API request handler.

        Constructs the full URL, makes the request, handles common errors,
        and returns the response object. Follows the same pattern as
        `PageIndexTreeClient._make_request()` for consistency.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.).
            endpoint: API endpoint path (e.g., "/doc/").
            **kwargs: Additional arguments to pass to requests.Session.request().
                If json data is provided, Content-Type will be set automatically.

        Returns:
            Response object from the API request.

        Raises:
            PageIndexAPIError: If request fails or returns error status codes.
        """
        # Guard: Prevent HTTP calls in SDK mode
        if self._use_sdk:
            error_msg = (
                f"HTTP method '{method}' called on endpoint '{endpoint}' in SDK mode. "
                "HTTP methods are not available when use_sdk=True. "
                "Use SDK client methods instead or set use_sdk=False to use "
                "HTTP API mode."
            )
            logger.error(error_msg)
            raise PageIndexAPIError(error_msg)

        # Ensure session is initialized (should always be true in HTTP mode)
        if self._session is None:
            error_msg = (
                "HTTP session is not initialized. This should not happen in HTTP mode. "
                "Please check client initialization."
            )
            logger.error(error_msg)
            raise PageIndexAPIError(error_msg)

        url = f"{self.config.base_url}{endpoint}"

        # Set Content-Type for JSON requests if json parameter is provided
        # and files is not provided (which would indicate multipart/form-data)
        if "json" in kwargs and "files" not in kwargs:
            kwargs.setdefault("headers", {}).update(
                {"Content-Type": "application/json"}
            )

        # Set default timeout if not explicitly provided
        kwargs.setdefault("timeout", self.DEFAULT_TIMEOUT)

        try:
            response = self._session.request(method, url, **kwargs)

            # Log request details
            logger.debug(f"API request: {method} {endpoint} -> {response.status_code}")

            # Handle common errors
            if response.status_code == 401 or response.status_code == 403:
                error_msg = (
                    f"Authentication failed: {response.status_code} {response.reason}"
                )
                logger.error(error_msg)
                raise PageIndexAPIError(error_msg)
            elif response.status_code == 404:
                error_msg = (
                    f"Resource not found: {response.status_code} {response.reason}"
                )
                logger.error(error_msg)
                raise PageIndexAPIError(error_msg)
            elif 400 <= response.status_code < 500:
                # Other 4xx client errors
                error_msg = f"Client error: {response.status_code} {response.reason}"
                logger.error(error_msg)
                raise PageIndexAPIError(error_msg)
            elif response.status_code >= 500:
                error_msg = f"Server error: {response.status_code} {response.reason}"
                logger.error(error_msg)
                raise PageIndexAPIError(error_msg)

            return response

        except requests.RequestException as e:
            error_msg = f"Request failed: {method} {endpoint} - {str(e)}"
            logger.error(error_msg)
            raise PageIndexAPIError(error_msg, original_exception=e) from e
        except PageIndexAPIError:
            # Re-raise API errors
            raise
        except Exception as e:
            error_msg = (
                f"Unexpected error in API request: {method} {endpoint} - {str(e)}"
            )
            logger.error(error_msg)
            raise PageIndexAPIError(error_msg, original_exception=e) from e

    def upload_pdf(self, pdf_bytes: bytes, filename: str) -> str:
        """Upload a PDF file to PageIndex API and get a document ID.

        This method uploads the PDF to PageIndex storage and returns a document
        identifier that can be used for subsequent OCR processing operations.

        Args:
            pdf_bytes: PDF file content as bytes.
            filename: Name of the PDF file (used for identification).

        Returns:
            Document identifier (doc_id) for use in subsequent processing operations.

        Raises:
            PageIndexUploadError: If file upload fails.
        """
        try:
            logger.info(f"Uploading PDF file: {filename}")

            # SDK mode: Use SDK client with temporary file
            if self._use_sdk:
                if self._sdk_client is None:
                    error_msg = "SDK client is not initialized"
                    logger.error(error_msg)
                    raise PageIndexAPIError(error_msg)
                try:
                    with temporary_pdf_file(pdf_bytes, filename) as temp_path:
                        # Call SDK submit_document with temp file path
                        result = self._sdk_client.submit_document(str(temp_path))

                        # Extract doc_id from SDK response
                        # SDK may return doc_id directly or in a response object
                        doc_id = None
                        if isinstance(result, str):
                            doc_id = result
                        elif isinstance(result, dict):
                            # Check common field names for doc_id
                            for field_name in ["doc_id", "id", "document_id"]:
                                if field_name in result:
                                    doc_id = str(result[field_name])
                                    break
                        else:
                            # Try to get doc_id as attribute if result is an object
                            for attr_name in ["doc_id", "id", "document_id"]:
                                if hasattr(result, attr_name):
                                    doc_id = str(getattr(result, attr_name))
                                    break

                        if not doc_id:
                            error_msg = (
                                f"Could not extract doc_id from SDK response: {result}"
                            )
                            logger.error(error_msg)
                            raise PageIndexUploadError(error_msg)

                        # Store metadata in internal state
                        self._uploaded_documents[doc_id] = {
                            "filename": filename,
                            "upload_time": time.time(),
                        }

                        logger.info(
                            f"Successfully uploaded file via SDK: {filename} "
                            f"(doc_id: {doc_id})"
                        )
                        return doc_id

                except Exception as e:
                    # Wrap SDK exceptions in PageIndexUploadError
                    error_msg = (
                        f"Failed to upload PDF file '{filename}' via SDK: {str(e)}"
                    )
                    logger.error(error_msg)
                    raise PageIndexUploadError(error_msg, original_exception=e) from e

            # HTTP mode: Use existing HTTP API flow
            # Prepare multipart/form-data request with PDF file
            files = {"file": (filename, pdf_bytes, "application/pdf")}

            # Make POST request to upload endpoint
            # requests library automatically sets Content-Type for multipart/form-data
            # when files parameter is provided, overriding session headers
            response = self._make_request("POST", "/doc/", files=files)

            # Parse JSON response to extract doc_id
            try:
                response_data = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse upload response: {str(e)}"
                logger.error(error_msg)
                raise PageIndexUploadError(error_msg, original_exception=e) from e

            # Extract doc_id from response (field name may vary based on API)
            # Common field names: "doc_id", "id", "document_id"
            doc_id = None
            for field_name in ["doc_id", "id", "document_id"]:
                if field_name in response_data:
                    doc_id = str(response_data[field_name])
                    break

            if not doc_id:
                error_msg = f"Could not find doc_id in upload response: {response_data}"
                logger.error(error_msg)
                raise PageIndexUploadError(error_msg)

            # Store metadata in internal state
            self._uploaded_documents[doc_id] = {
                "filename": filename,
                "upload_time": time.time(),
            }

            logger.info(f"Successfully uploaded file: {filename} (doc_id: {doc_id})")
            return doc_id

        except requests.RequestException as e:
            error_msg = f"Failed to upload PDF file '{filename}': {str(e)}"
            logger.error(error_msg)
            raise PageIndexUploadError(error_msg, original_exception=e) from e
        except PageIndexAPIError as e:
            # Convert API errors to upload errors for upload operations
            error_msg = f"Upload failed: {str(e)}"
            logger.error(error_msg)
            raise PageIndexUploadError(error_msg, original_exception=e) from e
        except PageIndexUploadError:
            # Re-raise upload errors
            raise
        except Exception as e:
            error_msg = f"Unexpected error uploading PDF file '{filename}': {str(e)}"
            logger.error(error_msg)
            raise PageIndexUploadError(error_msg, original_exception=e) from e

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

    def _validate_format_page_response(
        self, response_data: dict | list, doc_id: str
    ) -> None:
        """Validate format=page response structure before parsing.

        This method performs strict validation on the response structure to ensure
        it matches the expected format=page response format. It validates the
        response type, extracts pages data, and verifies each page has required
        fields (markdown and page_number).

        Args:
            response_data: JSON response data from PageIndex OCR API (dict or list).
            doc_id: Document identifier for error context.

        Raises:
            PageIndexOCRError: If response structure is invalid or missing
            required fields.
        """
        # FIX: Strict validation for format=page responses to catch API changes
        # early. This validation ensures the response structure matches expected
        # format (markdown + page_number) and provides detailed error messages
        # when the API returns unexpected data structures. Without this
        # validation, empty or malformed responses could silently fail
        # downstream.
        # Step 1: Check response type
        if not isinstance(response_data, (dict, list)):
            error_msg = (
                f"Invalid response type for doc_id {doc_id}: expected dict or "
                f"list, got {type(response_data)}"
            )
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg)

        # Step 2: Extract pages data
        pages_data = None
        if isinstance(response_data, dict):
            # Check for fields in order: "result", "pages", "data", "results"
            for field_name in ["result", "pages", "data", "results"]:
                if field_name in response_data:
                    pages_data = response_data[field_name]
                    break
            if pages_data is None:
                available_fields = list(response_data.keys())
                error_msg = (
                    f"No pages data found in response for doc_id {doc_id}. "
                    f"Available fields: {available_fields}"
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)
        else:
            # List response: use the list directly as pages data
            pages_data = response_data

        # Step 3: Validate pages data is list
        if not isinstance(pages_data, list):
            error_msg = (
                f"Pages data is not a list for doc_id {doc_id}: got {type(pages_data)}"
            )
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg)

        # Step 4: Validate each page structure
        for idx, page_data in enumerate(pages_data):
            if not isinstance(page_data, dict):
                error_msg = (
                    f"Invalid page structure at index {idx} for doc_id {doc_id}. "
                    f"Expected dict, got {type(page_data)}"
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            # Check for required fields
            if "markdown" not in page_data:
                error_msg = (
                    f"Invalid page structure at index {idx} for doc_id {doc_id}. "
                    f"Expected fields: markdown (str), page_number (int) or "
                    f"page_index (int). Actual: {list(page_data.keys())}"
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            # Accept either page_number or page_index (both are 1-based per
            # PageIndex API docs)
            if "page_number" not in page_data and "page_index" not in page_data:
                error_msg = (
                    f"Invalid page structure at index {idx} for doc_id {doc_id}. "
                    f"Expected fields: markdown (str), page_number (int) or "
                    f"page_index (int). Actual: {list(page_data.keys())}"
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            markdown = page_data["markdown"]
            # Get page_number from either page_number or page_index field
            # (both are 1-based). Use explicit None check to handle 0 values
            # correctly (though page_index should be 1-based)
            page_number = page_data.get("page_number")
            if page_number is None:
                page_number = page_data.get("page_index")
            if not isinstance(markdown, str):
                error_msg = (
                    f"Invalid page structure at index {idx} for doc_id {doc_id}: "
                    f"expected markdown (str), page_number/page_index (int). "
                    f"Actual: markdown={repr(markdown)} "
                    f"(type {type(markdown).__name__}), "
                    f"page_number/page_index={repr(page_number)} "
                    f"(type {type(page_number).__name__})."
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)
            if not isinstance(page_number, int):
                error_msg = (
                    f"Invalid page structure at index {idx} for doc_id {doc_id}: "
                    f"expected markdown (str), page_number/page_index (int). "
                    f"Actual: markdown={repr(markdown)} "
                    f"(type {type(markdown).__name__}), "
                    f"page_number/page_index={repr(page_number)} "
                    f"(type {type(page_number).__name__})."
                )
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

        # Step 5: Log validation success
        logger.debug(
            f"Successfully validated format=page response for doc_id {doc_id}: "
            f"{len(pages_data)} pages"
        )

    def _parse_ocr_response(self, response_data: dict | list) -> list[PageContent]:
        """Parse PageIndex OCR response into PageContent objects.

        Extracts pages array from response and converts each page into
        a PageContent object with markdown, images, and tables.

        Args:
            response_data: JSON response data from PageIndex OCR API.

        Returns:
            List of PageContent objects, one for each page in the PDF.

        Raises:
            PageIndexOCRError: If parsing fails or response structure is invalid.
        """
        try:
            # Extract pages array from response
            # Common field names: "result", "pages", "data", "results"
            pages_data = None
            if isinstance(response_data, dict):
                for field_name in ["result", "pages", "data", "results"]:
                    if field_name in response_data:
                        pages_data = response_data[field_name]
                        break

            if pages_data is None:
                # Try to use response_data directly if it's a list
                if isinstance(response_data, list):
                    pages_data = response_data
                else:
                    error_msg = (
                        f"Could not find pages array in OCR response: "
                        f"{list(response_data.keys())}"
                    )
                    logger.error(error_msg)
                    raise PageIndexOCRError(error_msg)

            if not isinstance(pages_data, list):
                error_msg = f"Pages data is not a list: {type(pages_data)}"
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            # Parse each page into PageContent objects
            pages = []
            for idx, page_data in enumerate(pages_data):
                try:
                    # Extract page_number (1-indexed) - accept either
                    # page_number or page_index (both are 1-based per PageIndex
                    # API docs). Use explicit None check to handle 0 values
                    # correctly (though page_index should be 1-based)
                    page_number = page_data.get("page_number")
                    if page_number is None:
                        page_number = page_data.get("page_index")
                    if page_number is None or not isinstance(page_number, int):
                        page_number = idx + 1

                    # Extract markdown content
                    markdown = page_data.get("markdown", "")
                    if not isinstance(markdown, str):
                        markdown = str(markdown) if markdown else ""

                    # Determine has_content using helper
                    has_content = not self._is_empty_page(markdown)

                    # Extract images list (base64 strings) if available
                    images = []
                    if "images" in page_data and isinstance(page_data["images"], list):
                        images = [
                            str(img)
                            if isinstance(img, str)
                            else str(img.get("base64", ""))
                            for img in page_data["images"]
                            if img
                        ]

                    # Extract tables list (markdown format) if available
                    tables = []
                    if "tables" in page_data and isinstance(page_data["tables"], list):
                        tables = [
                            str(tbl)
                            if isinstance(tbl, str)
                            else str(tbl.get("content", ""))
                            for tbl in page_data["tables"]
                            if tbl
                        ]

                    page_content = PageContent(
                        page_number=page_number,
                        markdown=markdown,
                        has_content=has_content,
                        images=images,
                        tables=tables,
                    )
                    pages.append(page_content)

                except Exception as e:
                    logger.warning(f"Failed to parse page {idx + 1}: {str(e)}")
                    # Continue processing other pages even if one fails
                    continue

            # Log parsing details
            content_count = sum(1 for p in pages if p.has_content)
            logger.debug(f"Parsed {len(pages)} pages ({content_count} with content)")

            if not pages:
                error_msg = "No pages found in OCR response"
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            return pages

        except PageIndexOCRError:
            # Re-raise OCR errors
            raise
        except Exception as e:
            error_msg = f"Failed to parse OCR response: {str(e)}"
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg, original_exception=e) from e

    def _is_processing_error(self, exception: Exception) -> bool:
        """Check if an exception indicates document is still processing (not ready).

        This helper method centralizes the logic for distinguishing between
        "retry later" (still processing) and "permanent failure" errors. This
        is used by `poll_ocr_results_batch()` to determine whether to retry a
        document or mark it as permanently failed.

        The method checks for HTTP 202 (Accepted) status codes and common processing
        keywords in error messages to identify transient processing states. It also
        checks wrapped SDK exceptions through the `original_exception` attribute.

        Args:
            exception: Exception object to check. Typically a `PageIndexOCRError`
                or `PageIndexAPIError` instance. May contain `original_exception`
                attribute for SDK exceptions.

        Returns:
            True if exception indicates document is still processing (not ready),
            False for other errors (actual failures that should not be retried).
        """
        # Check for PageIndexAPIError with status code 202 (Accepted)
        if isinstance(exception, PageIndexAPIError):
            # Check if the error message indicates 202 status
            error_msg = str(exception).lower()
            if "202" in error_msg or "accepted" in error_msg:
                return True

        # Check for PageIndexOCRError with original_exception (SDK exceptions)
        if isinstance(exception, PageIndexOCRError) and (
            hasattr(exception, "original_exception") and exception.original_exception
        ):
            original = exception.original_exception

            # Check if original exception is PageIndexAPIError or exposes
            # status_code/status field
            # Treat status code 202 (Accepted) as a processing state
            status_code = None
            if isinstance(original, PageIndexAPIError):
                # Check if PageIndexAPIError has status_code attribute
                status_code = getattr(original, "status_code", None)
                # Also check message for 202 status
                original_msg = str(original).lower()
                if "202" in original_msg or "accepted" in original_msg:
                    return True
            elif hasattr(original, "status_code"):
                # Check status_code attribute on original exception
                status_code = getattr(original, "status_code", None)
            elif hasattr(original, "status"):
                # Check status attribute (may be string like "accepted")
                status = getattr(original, "status", None)
                if status == 202 or (
                    isinstance(status, str) and status.lower() == "accepted"
                ):
                    return True

            # Check status code 202
            if status_code == 202:
                return True

            # Check original exception type and message for SDK-specific
            # processing indicators
            original_msg = str(original).lower()
            # Check both wrapper and original exception messages
            wrapper_msg = str(exception).lower()
            combined_msg = f"{wrapper_msg} {original_msg}"

            # SDK-specific processing keywords
            sdk_processing_keywords = [
                "processing",
                "not ready",
                "pending",
                "in progress",
                "still processing",
                "not_ready",
                "pending_processing",
            ]

            for keyword in sdk_processing_keywords:
                if keyword in combined_msg:
                    return True

        # Check exception message for processing indicators
        error_msg = str(exception).lower()
        processing_keywords = [
            "processing",
            "not ready",
            "pending",
            "in progress",
            "still processing",
        ]

        return any(keyword in error_msg for keyword in processing_keywords)

        return False

    def process_pdf(self, doc_id: str) -> list[PageContent]:
        """Process uploaded PDF and extract page content.

        This method retrieves OCR results for a previously uploaded PDF
        and processes the response into PageContent objects. Each page
        is analyzed for content and structured data (images, tables) is extracted.

        Args:
            doc_id: Document identifier returned from upload_pdf() method.

        Returns:
            List of PageContent objects, one for each page in the PDF. Pages are
            1-indexed and include markdown content, images, and tables.

        Raises:
            PageIndexOCRError: If OCR processing fails, response parsing fails, or
                document ID is not found in uploaded documents.
        """
        try:
            # Verify doc_id exists in uploaded documents
            if doc_id not in self._uploaded_documents:
                error_msg = f"Document ID {doc_id} not found in uploaded documents"
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            # SDK mode: Use SDK client get_ocr method
            if self._use_sdk:
                if self._sdk_client is None:
                    error_msg = "SDK client is not initialized"
                    logger.error(error_msg)
                    raise PageIndexOCRError(error_msg)
                try:
                    logger.info(f"Processing PDF via SDK (doc_id: {doc_id})")

                    # Call SDK get_ocr with format="page"
                    response_data = self._sdk_client.get_ocr(doc_id, format="page")

                    # Convert SDK response to dict/list if needed
                    if not isinstance(response_data, (dict, list)):
                        # If SDK returns an object, try to extract data
                        if hasattr(response_data, "__dict__"):
                            response_data = response_data.__dict__
                        elif hasattr(response_data, "result"):
                            response_data = response_data.result
                        else:
                            error_msg = (
                                f"Unexpected SDK response type: {type(response_data)}"
                            )
                            logger.error(error_msg)
                            raise PageIndexOCRError(error_msg)

                    # Check SDK status before validation: detect non-completed
                    # status to trigger retries
                    # Status can be in dict response or as object attribute
                    status = None
                    if isinstance(response_data, dict):
                        status = response_data.get("status")
                    elif hasattr(response_data, "status"):
                        status = getattr(response_data, "status", None)

                    # If status exists and is not completed, raise processing
                    # error for retry logic
                    if status is not None and status != "completed":
                        # Status values like "processing", "pending", etc.
                        # indicate document is still processing
                        error_msg = (
                            f"Document {doc_id} is still processing (status: {status})"
                        )
                        logger.debug(error_msg)
                        raise PageIndexOCRError(error_msg)

                    # Validate response structure
                    self._validate_format_page_response(response_data, doc_id)

                    # Parse response into PageContent objects
                    pages = self._parse_ocr_response(response_data)

                    logger.info(
                        f"Successfully processed PDF via SDK: {len(pages)} pages "
                        f"extracted"
                    )
                    return pages

                except Exception as e:
                    # Wrap SDK exceptions in PageIndexOCRError
                    error_msg = (
                        f"Failed to process PDF via SDK (doc_id: {doc_id}): {str(e)}"
                    )
                    logger.error(error_msg)
                    raise PageIndexOCRError(error_msg, original_exception=e) from e

            # HTTP mode: Use existing HTTP API flow
            logger.info(f"Processing PDF via OCR API (doc_id: {doc_id})")

            # GET from OCR endpoint
            # Use format=page instead of format=node because format=node
            # produced empty markdown in Zotero notes. format=page is the fix
            # that ensures markdown content is properly populated.
            response = self._make_request("GET", f"/doc/{doc_id}/?type=ocr&format=page")

            # Check for 202 Accepted status (still processing)
            if response.status_code == 202:
                error_msg = f"Document {doc_id} is still processing (202 Accepted)"
                logger.debug(error_msg)
                raise PageIndexOCRError(error_msg)

            # Parse JSON response
            try:
                response_data = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse OCR response: {str(e)}"
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg, original_exception=e) from e

            # Guard against list-style responses: skip status evaluation and
            # pass directly to parser
            if isinstance(response_data, list):
                # List response: preserve backward compatibility by passing
                # directly to parser
                self._validate_format_page_response(response_data, doc_id)
                pages = self._parse_ocr_response(response_data)
            elif isinstance(response_data, dict):
                # Dict response: check status field
                status = response_data.get("status")
                if status and status != "completed":
                    # Document is still processing
                    doc_id_from_response = response_data.get("doc_id", doc_id)
                    error_msg = (
                        f"Document {doc_id_from_response} is still processing "
                        f"(status: {status})"
                    )
                    logger.debug(error_msg)
                    raise PageIndexOCRError(error_msg)

                # Extract result field when status is "completed" or status
                # field is missing
                if status == "completed" and "result" in response_data:
                    pages_data = response_data["result"]
                else:
                    # Fallback to response_data for backward compatibility
                    pages_data = response_data

                # Validate response structure before parsing
                self._validate_format_page_response(pages_data, doc_id)

                # Parse response into PageContent objects
                pages = self._parse_ocr_response(pages_data)
            else:
                # Invalid response type
                error_msg = f"Invalid OCR response type: {type(response_data)}"
                logger.error(error_msg)
                raise PageIndexOCRError(error_msg)

            logger.info(f"Successfully processed PDF: {len(pages)} pages extracted")
            return pages

        except requests.RequestException as e:
            error_msg = f"Failed to process PDF via OCR API: {str(e)}"
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg, original_exception=e) from e
        except PageIndexAPIError as e:
            # Convert API errors to OCR errors for processing operations
            error_msg = f"OCR processing failed: {str(e)}"
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg, original_exception=e) from e
        except PageIndexOCRError:
            # Re-raise OCR errors
            raise
        except Exception as e:
            error_msg = f"Unexpected error processing PDF via OCR API: {str(e)}"
            logger.error(error_msg)
            raise PageIndexOCRError(error_msg, original_exception=e) from e

    def cleanup_file(self, doc_id: str) -> None:
        """Delete uploaded file from PageIndex storage.

        This method removes the file from PageIndex storage after processing
        is complete. This is optional but recommended to avoid accumulating
        unused files in your PageIndex account.

        Args:
            doc_id: Unique identifier for the document to delete (from upload_pdf).

        Note:
            This method logs errors but does not raise exceptions, as file
            cleanup is non-critical and should not fail the main processing flow.
        """
        try:
            logger.info(f"Cleaning up file: {doc_id}")

            # SDK mode: Use SDK client delete_document method
            if self._use_sdk:
                if self._sdk_client is None:
                    logger.warning("SDK client is not initialized, skipping cleanup")
                    return
                try:
                    self._sdk_client.delete_document(doc_id)

                    # Remove doc_id from internal state if exists
                    if doc_id in self._uploaded_documents:
                        del self._uploaded_documents[doc_id]

                    logger.info(f"Successfully deleted file via SDK: {doc_id}")

                except Exception as e:
                    # Log SDK exceptions as warnings without raising
                    logger.warning(f"Failed to delete file {doc_id} via SDK: {str(e)}")
                return

            # HTTP mode: Use existing HTTP API flow
            # DELETE from cleanup endpoint
            self._make_request("DELETE", f"/doc/{doc_id}/")

            # Remove doc_id from internal state if exists
            if doc_id in self._uploaded_documents:
                del self._uploaded_documents[doc_id]

            logger.info(f"Successfully deleted file: {doc_id}")

        except Exception as e:
            # Log but don't raise - cleanup failures are non-critical
            logger.warning(f"Failed to delete file {doc_id}: {str(e)}")

    def upload_pdfs_batch(
        self, pdfs: list[tuple[bytes, str, str, str]]
    ) -> list[UploadedDocument]:
        """Upload multiple PDFs in batch and return list of uploaded documents.

        This method uploads multiple PDFs sequentially without waiting for
        processing to complete. Each PDF is uploaded and an UploadedDocument
        is created for successful uploads. The method continues uploading
        remaining PDFs when one fails, collecting and logging failures instead
        of raising immediately.

        This method supports both HTTP API and SDK modes. The mode is determined
        by the `config.use_sdk` flag set during client initialization. SDK mode
        uses temporary files for upload operations.

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
        logger.info(
            f"Uploading {len(pdfs)} PDFs in batch "
            f"({'SDK mode' if self._use_sdk else 'HTTP API mode'})"
        )

        uploaded_docs = []
        total = len(pdfs)
        errors = []

        for index, (pdf_bytes, filename, item_key, attachment_key) in enumerate(
            pdfs, start=1
        ):
            try:
                doc_id = self.upload_pdf(pdf_bytes, filename)

                # Get upload metadata from internal state
                upload_metadata = self._uploaded_documents.get(doc_id, {})
                upload_time = upload_metadata.get("upload_time", time.time())

                # Create UploadedDocument object
                uploaded_doc = UploadedDocument(
                    doc_id=doc_id,
                    filename=filename,
                    upload_time=upload_time,
                    item_key=item_key,  # Set from 4-tuple metadata
                    attachment_key=attachment_key,  # Set from 4-tuple metadata
                )
                uploaded_docs.append(uploaded_doc)
                logger.info(f"Uploaded {filename} ({index}/{total})")

            except PageIndexUploadError as e:
                # Log error but continue with remaining PDFs
                error_msg = f"Failed to upload {filename} in batch: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                continue
            except Exception as e:
                # Convert unexpected errors to upload errors and log
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

    def poll_ocr_results_batch(
        self,
        doc_ids: list[str],
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, list[PageContent]]:
        """Poll OCR results for multiple documents with exponential backoff.

        This method polls OCR results for multiple documents using a FIFO queue
        with exponential backoff. Documents are polled sequentially, and each
        document has independent timeout and max attempts limits. Successful results
        are preserved even when some documents fail, with errors logged separately.

        The polling algorithm uses exponential backoff starting at
        `self.config.polling_initial_interval` seconds, doubling on each retry
        until reaching `self.config.polling_max_interval`. Each document is polled
        independently with its own timeout (`self.config.polling_timeout`) and
        max attempts (`self.config.polling_max_attempts`).

        This method supports both HTTP API and SDK modes. The mode is determined
        by the `config.use_sdk` flag set during client initialization. SDK mode
        uses the SDK's `get_ocr()` method for polling operations.

        Args:
            doc_ids: List of document identifiers to poll for results.
            progress_callback: Optional callback function that reports
                per-document status transitions. Called with (doc_id, status)
                where status is one of: "processing" (document begins
                processing), "completed" (document processing completed
                successfully), "failed" (document processing failed
                permanently). Defaults to None (no progress reporting).

        Returns:
            Dictionary mapping doc_id to list of `PageContent` objects for that
            document.
            Only successfully processed documents are included in the dictionary.
            Failed documents are logged but not included in the result, allowing
            partial success.
        """
        logger.info(
            f"Polling OCR results for {len(doc_ids)} documents "
            f"({'SDK mode' if self._use_sdk else 'HTTP API mode'})"
        )

        # Initialize tracking structures
        results: dict[str, list[PageContent]] = {}
        pending_docs: deque[str] = deque(doc_ids)  # FIFO queue
        polling_state: dict[str, dict] = {}
        errors: dict[str, str] = {}
        processing_reported: set = (
            set()
        )  # Track which documents have had "processing" reported

        # Initialize polling state for all documents with per-document start times
        for doc_id in doc_ids:
            polling_state[doc_id] = {
                "attempts": 0,
                "start_time": time.time(),
                "last_poll_time": time.time(),
            }

        # Polling loop - FIFO processing
        # Documents are processed in first-in-first-out order to ensure fairness
        while pending_docs:
            # Get next doc_id from front of queue (FIFO)
            doc_id = pending_docs.popleft()
            state = polling_state[doc_id]

            # Report "processing" status on first attempt
            if doc_id not in processing_reported:
                if progress_callback:
                    progress_callback(doc_id, "processing")
                processing_reported.add(doc_id)

            # Check timeout: each document has independent timeout tracking
            elapsed = time.time() - state["start_time"]
            if elapsed > self.config.polling_timeout:
                error_msg = f"Document {doc_id} timed out after {elapsed:.1f}s"
                logger.error(error_msg)
                errors[doc_id] = error_msg
                # Report failure
                if progress_callback:
                    progress_callback(doc_id, "failed")
                continue

            # Check max attempts: prevent infinite retry loops
            if state["attempts"] >= self.config.polling_max_attempts:
                error_msg = (
                    f"Document {doc_id} exceeded max attempts ({state['attempts']})"
                )
                logger.error(error_msg)
                errors[doc_id] = error_msg
                # Report failure
                if progress_callback:
                    progress_callback(doc_id, "failed")
                continue

            # Calculate exponential backoff delay: 2^attempts * initial_interval,
            # capped at max_interval. Example: initial=2s, attempts=0 -> 2s,
            # attempts=1 -> 4s, attempts=2 -> 8s, etc.
            backoff_delay = min(
                self.config.polling_initial_interval * (2 ** state["attempts"]),
                self.config.polling_max_interval,
            )

            # Apply delay if needed
            time_since_last_poll = time.time() - state["last_poll_time"]
            if time_since_last_poll < backoff_delay:
                sleep_time = backoff_delay - time_since_last_poll
                logger.debug(
                    f"Waiting {sleep_time:.1f}s before polling {doc_id} (backoff)"
                )
                time.sleep(sleep_time)

            # Try to process document
            try:
                logger.debug(
                    f"Polling {doc_id} "
                    f"(attempt {state['attempts'] + 1}/"
                    f"{self.config.polling_max_attempts}, "
                    f"backoff {backoff_delay}s)"
                )

                pages = self.process_pdf(doc_id)

                # Success: store result and remove from pending
                results[doc_id] = pages
                logger.info(f"Document {doc_id} ready ({len(pages)} pages)")

                # Report completion
                if progress_callback:
                    progress_callback(doc_id, "completed")

            except PageIndexOCRError as e:
                # Check if this is a "still processing" error
                if self._is_processing_error(e):
                    # Still processing: increment attempts and add back to queue
                    state["attempts"] += 1
                    state["last_poll_time"] = time.time()
                    pending_docs.append(doc_id)  # Add to end of queue (FIFO)
                    logger.warning(
                        f"Document {doc_id} still processing "
                        f"(attempt {state['attempts']}/"
                        f"{self.config.polling_max_attempts}), will retry"
                    )
                else:
                    # Permanent error: log and remove from pending
                    error_msg = f"Document {doc_id} failed: {str(e)}"
                    logger.error(error_msg)
                    errors[doc_id] = error_msg
                    # Report failure
                    if progress_callback:
                        progress_callback(doc_id, "failed")
                    # Error isolation: continue with remaining documents

            except Exception as e:
                # Other exceptions: log error and remove from pending
                error_msg = f"Document {doc_id} encountered unexpected error: {str(e)}"
                logger.error(error_msg)
                errors[doc_id] = error_msg
                # Report failure
                if progress_callback:
                    progress_callback(doc_id, "failed")
                # Error isolation: continue with remaining documents

            # Apply batch delay between documents
            if pending_docs:
                time.sleep(self.config.polling_batch_delay)

        # Log summary
        success_count = len(results)
        failure_count = len(errors)

        if failure_count > 0:
            logger.warning(
                f"Batch polling completed with {failure_count} failure(s): "
                f"{success_count} succeeded, {failure_count} failed"
            )
        else:
            logger.info(f"Successfully polled {success_count}/{len(doc_ids)} documents")

        return results
