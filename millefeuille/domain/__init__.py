"""Domain models and configuration schemas

This module provides the domain layer for the Millefeuille,
including type-safe configuration schemas and domain models.

Public exports resolve on first access. Importing this package alone does not
load optional provider clients. Explicit provider exports (including star imports)
retain their real dependency errors; no fallback or replacement is installed.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import (
        AppConfig,
        ConfigError,
        MistralOCRConfig,
        OCRProviderConfig,
        PageIndexOCRConfig,
        ProcessingConfig,
        StorageConfig,
        ZoteroConfig,
        register_configs,
    )
    from .markdown_converter import MarkdownConverter, convert_markdown_to_html
    from .models import (
        NotePayload,
        PageContent,
        ProcessingResult,
    )
    from .note_formatter import NoteFormatter

_EXPORT_MODULES = {
    "AppConfig": "config",
    "ConfigError": "config",
    "MistralOCRConfig": "config",
    "OCRProviderConfig": "config",
    "PageIndexOCRConfig": "config",
    "ProcessingConfig": "config",
    "StorageConfig": "config",
    "ZoteroConfig": "config",
    "register_configs": "config",
    "MarkdownConverter": "markdown_converter",
    "convert_markdown_to_html": "markdown_converter",
    "NotePayload": "models",
    "PageContent": "models",
    "ProcessingResult": "models",
    "NoteFormatter": "note_formatter",
}

__all__ = [
    "ZoteroConfig",
    "OCRProviderConfig",
    "MistralOCRConfig",
    "PageIndexOCRConfig",
    "ProcessingConfig",
    "StorageConfig",
    "AppConfig",
    "register_configs",
    "ConfigError",
    "PageContent",
    "NotePayload",
    "ProcessingResult",
    "NoteFormatter",
    "convert_markdown_to_html",
    "MarkdownConverter",
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
