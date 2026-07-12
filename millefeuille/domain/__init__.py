"""Domain models and configuration schemas

This module provides the domain layer for the Millefeuille,
including type-safe configuration schemas and domain models.
"""

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
