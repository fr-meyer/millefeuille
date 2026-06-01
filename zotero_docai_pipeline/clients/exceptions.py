"""
Custom exception classes for client operations (Zotero and Mistral).

This module defines domain-specific exceptions that provide clear error context
for API interactions, making error handling and debugging easier in the
orchestration layer.

Exception Hierarchy:
- OCRClientError (base for all OCR providers)
  ├── MistralClientError
  │   ├── MistralAPIError
  │   ├── MistralUploadError
  │   ├── MistralOCRError
  │   └── NoteSizeExceededError
  └── PageIndexClientError (Phase 2)
      ├── PageIndexAPIError
      ├── PageIndexUploadError
      ├── PageIndexOCRError
      └── PageIndexTreeError
"""


class OCRClientError(Exception):
    """Base exception for all OCR client errors.

    All OCR provider-specific exceptions (Mistral, PageIndex, etc.) inherit from
    this class, allowing for broad exception catching when needed while maintaining
    specific error types for precise error handling. This provides a unified
    exception hierarchy for all OCR-related operations.

    All OCR provider-specific exceptions should inherit from this class.
    """

    def __init__(self, message: str, original_exception: Exception | None = None):
        """Initialize the exception.

        Args:
            message: Human-readable error message describing what went wrong.
            original_exception: Optional original exception that caused this error.
        """
        super().__init__(message)
        self.message = message
        self.original_exception = original_exception

    def __str__(self) -> str:
        """Return string representation with context."""
        if self.original_exception:
            orig_type = type(self.original_exception).__name__
            orig_msg = str(self.original_exception)
            return f"{self.message} (Original: {orig_type}: {orig_msg})"
        return self.message


class ZoteroClientError(Exception):
    """Base exception for all Zotero client errors.

    All Zotero-related exceptions inherit from this class, allowing for
    broad exception catching when needed while maintaining specific error types
    for precise error handling.
    """

    def __init__(self, message: str, original_exception: Exception | None = None):
        """Initialize the exception.

        Args:
            message: Human-readable error message describing what went wrong.
            original_exception: Optional original exception that caused this error.
        """
        super().__init__(message)
        self.message = message
        self.original_exception = original_exception

    def __str__(self) -> str:
        """Return string representation with context."""
        if self.original_exception:
            orig_type = type(self.original_exception).__name__
            orig_msg = str(self.original_exception)
            return f"{self.message} (Original: {orig_type}: {orig_msg})"
        return self.message


class ZoteroAPIError(ZoteroClientError):
    """Exception raised for Zotero API communication failures.

    This exception is raised when there are network errors, rate limits,
    server errors (5xx), or other API communication issues that prevent
    successful interaction with the Zotero API.
    """

    pass


class ZoteroAuthError(ZoteroClientError):
    """Exception raised for authentication/authorization failures.

    This exception is raised when the API key is invalid, expired, or
    the user doesn't have sufficient permissions to perform the requested
    operation (401 Unauthorized, 403 Forbidden).
    """

    pass


class ZoteroItemNotFoundError(ZoteroClientError):
    """Exception raised when a requested item or attachment is not found.

    This exception is raised when attempting to access items, attachments,
    or other resources that don't exist in the Zotero library (404 Not Found).
    """

    pass


class MistralClientError(OCRClientError):
    """Base exception for all Mistral client errors.

    All Mistral-related exceptions inherit from this class, allowing for
    broad exception catching when needed while maintaining specific error types
    for precise error handling.

    This exception inherits from OCRClientError, providing a unified exception
    hierarchy for all OCR providers.
    """

    def __init__(self, message: str, original_exception: Exception | None = None):
        """Initialize the exception.

        Args:
            message: Human-readable error message describing what went wrong.
            original_exception: Optional original exception that caused this error.
        """
        super().__init__(message, original_exception)
        self.message = message
        self.original_exception = original_exception

    def __str__(self) -> str:
        """Return string representation with context."""
        if self.original_exception:
            orig_type = type(self.original_exception).__name__
            orig_msg = str(self.original_exception)
            return f"{self.message} (Original: {orig_type}: {orig_msg})"
        return self.message


class MistralAPIError(MistralClientError):
    """Exception raised for Mistral API communication failures.

    This exception is raised when there are network errors, rate limits,
    server errors (5xx), or other API communication issues that prevent
    successful interaction with the Mistral API.
    """

    pass


class MistralUploadError(MistralClientError):
    """Exception raised for file upload failures.

    This exception is raised when file upload to Mistral Files API fails
    or when signed URL retrieval fails.
    """

    pass


class MistralOCRError(MistralClientError):
    """Exception raised for OCR processing failures.

    This exception is raised when OCR processing fails or response parsing fails.
    """

    pass


class NoteSizeExceededError(MistralClientError):
    """Exception raised when note content exceeds Zotero's size limits.

    This exception is raised during validation of figure or table notes that cannot
    be split when the content size exceeds Zotero's maximum note size limit
    (typically 250KB). This prevents API failures and provides clear feedback
    about content that needs to be reduced in size.

    When raised:
        During validation of figure/table notes that exceed size limits and cannot
        be automatically split into smaller chunks.

    Context:
        Error message includes filename, page number, element type, actual size,
        and threshold to help users identify and resolve the issue.

    Resolution:
        User must reduce image quality, compress content, or split content manually
        before processing.
    """

    def __init__(
        self,
        message: str,
        filename: str,
        page_number: int,
        element_type: str,
        actual_size: int,
        threshold: int,
        original_exception: Exception | None = None,
    ):
        """Initialize the exception.

        Args:
            message: Human-readable error message describing what went wrong.
            filename: Name of the file containing the element that exceeded the limit.
            page_number: Page number where the element is located.
            element_type: Type of element that exceeded the limit ("figure" or "table").
            actual_size: Actual size of the element content in bytes (character count).
            threshold: Maximum allowed size in bytes (character count).
            original_exception: Optional original exception that caused this error.
        """
        super().__init__(message, original_exception)
        self.message = message
        self.filename = filename
        self.page_number = page_number
        self.element_type = element_type
        self.actual_size = actual_size
        self.threshold = threshold
        self.original_exception = original_exception

    def __str__(self) -> str:
        """Return string representation with context."""
        error_msg = (
            f"{self.message} "
            f"[File: {self.filename}, Page: {self.page_number}, "
            f"Type: {self.element_type}, Size: {self.actual_size} bytes, "
            f"Threshold: {self.threshold} bytes]"
        )
        if self.original_exception:
            error_msg += (
                f" (Original: {type(self.original_exception).__name__}: "
                f"{str(self.original_exception)})"
            )
        return error_msg


class PageIndexClientError(OCRClientError):
    """Base exception for all PageIndex client errors.

    Placeholder for Phase 2 integration.

    All PageIndex-related exceptions inherit from this class, allowing for
    broad exception catching when needed while maintaining specific error types
    for precise error handling.

    This exception inherits from OCRClientError, providing a unified exception
    hierarchy for all OCR providers.
    """

    def __init__(self, message: str, original_exception: Exception | None = None):
        """Initialize the exception.

        Args:
            message: Human-readable error message describing what went wrong.
            original_exception: Optional original exception that caused this error.
        """
        super().__init__(message, original_exception)
        self.message = message
        self.original_exception = original_exception

    def __str__(self) -> str:
        """Return string representation with context."""
        if self.original_exception:
            orig_type = type(self.original_exception).__name__
            orig_msg = str(self.original_exception)
            return f"{self.message} (Original: {orig_type}: {orig_msg})"
        return self.message


class PageIndexAPIError(PageIndexClientError):
    """Exception raised for PageIndex API communication failures.

    This exception is raised when there are network errors, rate limits,
    server errors (5xx), or other API communication issues that prevent
    successful interaction with the PageIndex API.
    """

    pass


class PageIndexUploadError(PageIndexClientError):
    """Exception raised for file upload failures.

    This exception is raised when file upload to PageIndex API fails
    or when signed URL retrieval fails.
    """

    pass


class PageIndexOCRError(PageIndexClientError):
    """Exception raised for OCR processing failures.

    This exception is raised when OCR processing fails or response parsing fails.
    """

    pass


class PageIndexTreeError(PageIndexClientError):
    """Exception raised for tree structure extraction failures.

    This exception is raised when tree structure extraction fails, including:
    - Tree structure extraction failures during OCR processing
    - Tree API communication errors (network, rate limits, server errors)
    - Tree response parsing failures (invalid JSON, missing fields, malformed data)
    - Markdown-to-tree conversion failures (parsing errors, structure validation)

    Tree structure extraction occurs when enabled in configuration and involves
    converting document markdown or structured content into a hierarchical tree
    representation. This exception provides clear error context for debugging
    tree extraction issues.
    """

    pass


class TreeStructureProcessingError(OCRClientError):
    """Exception raised for tree structure processing failures.

    This provider-neutral exception is raised when tree structure processing fails,
    regardless of the underlying provider (PageIndex, future providers). It provides
    a generic interface for tree structure operations while allowing provider-specific
    exceptions to be re-raised without forcing them into provider-specific error types.

    This exception is raised by TreeStructureProcessor when tree extraction
    or processing fails. The original client exceptions are preserved and
    can be accessed via the
    original_exception attribute.

    Tree structure processing occurs when enabled in configuration and involves
    extracting hierarchical document structures (table of contents, sections, etc.)
    from OCR results or markdown content.
    """

    pass


class HandoffSecurityError(Exception):
    """Raised when a handoff row fails the red-list security check."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class AttachmentIdentityError(Exception):
    """Raised when an attachment fails identity verification for handoff export."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message
