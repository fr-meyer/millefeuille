"""External API clients (Zotero, Mistral).

This module provides client wrappers for external APIs used by the pipeline,
including the Zotero API client for library interactions and the Mistral AI
OCR client for document processing, along with custom exception classes for
error handling.
"""

from .exceptions import (
    MistralAPIError,
    MistralClientError,
    MistralOCRError,
    MistralUploadError,
    NoteSizeExceededError,
    OCRClientError,
    PageIndexAPIError,
    PageIndexClientError,
    PageIndexOCRError,
    PageIndexTreeError,
    PageIndexUploadError,
    ZoteroAPIError,
    ZoteroAuthError,
    ZoteroClientError,
    ZoteroItemNotFoundError,
)
from .mistral_client import MistralClient
from .ocr_client import OCRClient, OCRProvider
from .pageindex_client import PageIndexClient
from .pageindex_tree_client import PageIndexTreeClient
from .temp_file_utils import temporary_pdf_file
from .tree_client import TreeClient
from .zotero_client import ZoteroClient

__all__ = [
    "OCRClient",
    "OCRProvider",
    "OCRClientError",
    "MistralClient",
    "MistralClientError",
    "MistralUploadError",
    "MistralOCRError",
    "MistralAPIError",
    "NoteSizeExceededError",
    "PageIndexClient",
    "PageIndexClientError",
    "PageIndexAPIError",
    "PageIndexUploadError",
    "PageIndexOCRError",
    "PageIndexTreeClient",
    "PageIndexTreeError",
    "temporary_pdf_file",
    "TreeClient",
    "ZoteroClient",
    "ZoteroClientError",
    "ZoteroAPIError",
    "ZoteroAuthError",
    "ZoteroItemNotFoundError",
]
