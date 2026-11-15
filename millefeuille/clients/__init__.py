"""External API clients (Zotero, Mistral).

This module provides client wrappers for external APIs used by the pipeline,
including the Zotero API client for library interactions and the Mistral AI
OCR client for document processing, along with custom exception classes for
error handling.

Public exports resolve on first access. Importing this package alone does not
load optional provider clients. Explicit provider exports (including star imports)
retain their real dependency errors; no fallback or replacement is installed.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    from .mistral_client import MistralClient, MistralOCRExecution
    from .ocr_client import OCRClient, OCRProvider
    from .pageindex_client import PageIndexClient
    from .pageindex_tree_client import PageIndexTreeClient
    from .temp_file_utils import temporary_pdf_file
    from .tree_client import TreeClient
    from .zotero_client import ZoteroClient

_EXPORT_MODULES = {
    "MistralAPIError": "exceptions",
    "MistralClientError": "exceptions",
    "MistralOCRError": "exceptions",
    "MistralUploadError": "exceptions",
    "NoteSizeExceededError": "exceptions",
    "OCRClientError": "exceptions",
    "PageIndexAPIError": "exceptions",
    "PageIndexClientError": "exceptions",
    "PageIndexOCRError": "exceptions",
    "PageIndexTreeError": "exceptions",
    "PageIndexUploadError": "exceptions",
    "ZoteroAPIError": "exceptions",
    "ZoteroAuthError": "exceptions",
    "ZoteroClientError": "exceptions",
    "ZoteroItemNotFoundError": "exceptions",
    "MistralClient": "mistral_client",
    "MistralOCRExecution": "mistral_client",
    "OCRClient": "ocr_client",
    "OCRProvider": "ocr_client",
    "PageIndexClient": "pageindex_client",
    "PageIndexTreeClient": "pageindex_tree_client",
    "temporary_pdf_file": "temp_file_utils",
    "TreeClient": "tree_client",
    "ZoteroClient": "zotero_client",
}

__all__ = [
    "OCRClient",
    "OCRProvider",
    "OCRClientError",
    "MistralClient",
    "MistralOCRExecution",
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


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
