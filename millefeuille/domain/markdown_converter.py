"""
Markdown to HTML conversion for Zotero note content.

This module provides utilities to convert markdown content (from Mistral OCR)
into HTML format that Zotero's rich text editor (TinyMCE) can properly display.
The converter preserves base64 image data URIs and handles mixed markdown/HTML content.

Example usage:
    >>> from millefeuille.domain.markdown_converter import (
    ...     convert_markdown_to_html
    ... )
    >>>
    >>> markdown = "# Title\\n\\nContent with **bold** text"
    >>> html = convert_markdown_to_html(markdown)
    >>> print(html)
    '<h1>Title</h1>\\n<p>Content with <strong>bold</strong> text</p>'
"""

import logging
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

logger = logging.getLogger(__name__)


class MarkdownConverter:
    """Converts markdown content to HTML for Zotero notes.

    This converter is specifically configured to handle content from Mistral OCR,
    including:
    - Base64 image data URIs (both markdown and HTML img tags)
    - Markdown tables (GFM-style)
    - HTML tables (when already in HTML format)
    - Code blocks with syntax highlighting support
    - Headings, lists, emphasis, links

    The output HTML is compatible with Zotero's TinyMCE rich text editor.
    """

    def __init__(
        self,
        enable_table: bool = True,
        enable_strikethrough: bool = True,
        enable_tasklist: bool = True,
        html: bool = True,
        linkify: bool = True,
        breaks: bool = True,
    ) -> None:
        """Initialize the markdown converter with specified options.

        Args:
            enable_table: If True, enable GFM-style table parsing. Defaults to True.
            enable_strikethrough: If True, enable strikethrough text support (~~text~~).
                Defaults to True.
            enable_tasklist: If True, enable task list support (checkbox lists).
                Defaults to True.
            html: If True, allow raw HTML in markdown content. Defaults to True.
            linkify: If True, automatically convert URLs to links. Defaults to True.
            breaks: If True, convert single newlines to <br> tags. Defaults to True.
        """
        # Initialize MarkdownIt with GFM-like preset
        self.md = MarkdownIt("gfm-like")

        # Configure options
        self.md.options.update(
            {
                "html": html,
                "linkify": linkify,
                "breaks": breaks,
                "typographer": True,
            }
        )

        # Enable/disable built-in features
        if enable_table:
            self.md.enable(["table"])
        else:
            self.md.disable(["table"])

        if enable_strikethrough:
            self.md.enable(["strikethrough"])
        else:
            self.md.disable(["strikethrough"])

        # Use plugin for tasklists
        if enable_tasklist:
            self.md.use(tasklists_plugin)

    def convert(self, markdown_content: str) -> str:
        """Convert markdown content to HTML.

        This method handles the full conversion pipeline:
        1. Converts markdown to HTML using markdown-it-py
        2. Preserves base64 image data URIs
        3. Normalizes HTML output for Zotero compatibility

        Args:
            markdown_content: The markdown string to convert. Can contain mixed
                markdown and HTML content, base64 image data URIs, tables, code blocks,
                and other GFM features.

        Returns:
            The converted HTML string, ready for Zotero's TinyMCE editor. Returns
            an empty string if input is empty or whitespace-only.

        Example:
            >>> converter = MarkdownConverter()
            >>> markdown = "# Heading\\n\\nParagraph with **bold** text"
            >>> html = converter.convert(markdown)
            >>> print(html)
            '<h1>Heading</h1>\\n<p>Paragraph with <strong>bold</strong> text</p>'
        """
        # Check for empty or whitespace-only content
        if not markdown_content or not markdown_content.strip():
            return ""

        # Convert markdown to HTML
        html_output = self.md.render(markdown_content)

        # Preserve base64 images
        html_output = self._preserve_base64_images(html_output)

        # Normalize HTML
        html_output = self._normalize_html(html_output)

        return html_output

    def _preserve_base64_images(self, html: str) -> str:
        """Preserve base64 image data URIs in HTML output.

        This is a safety check to ensure that base64 image data URIs are not
        corrupted during the markdown-to-HTML conversion process. The method
        checks for data URIs and ensures <img> tags are properly formatted.

        Args:
            html: The HTML string to process.

        Returns:
            The HTML string with preserved base64 image data URIs.
        """
        # Pattern for base64 data URIs
        data_uri_pattern = r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+"

        # Check if any data URIs exist
        if re.search(data_uri_pattern, html):
            # Ensure <img> tags with data URIs are properly formatted
            # Normalize img tags: <img src="data:..." ...> -> <img src="data:..." ...>
            html = re.sub(
                r'<img\s+src="(data:image/[^"]+)"([^>]*)>',
                r'<img src="\1"\2>',
                html,
                flags=re.IGNORECASE,
            )

        return html

    def _normalize_html(self, html: str) -> str:
        """Normalize HTML output for Zotero compatibility.

        Performs cleanup operations to ensure the HTML is compatible with
        Zotero's TinyMCE editor:
        - Removes excessive blank lines (more than 2 consecutive newlines)
        - Normalizes whitespace around block elements
        - Strips leading/trailing whitespace

        Args:
            html: The HTML string to normalize.

        Returns:
            The normalized HTML string.
        """
        # Remove excessive blank lines (more than 2 consecutive newlines)
        html = re.sub(r"\n{3,}", "\n\n", html)

        # Normalize whitespace around block elements
        html = re.sub(r">\s+<", ">\n<", html)

        # Strip leading/trailing whitespace
        html = html.strip()

        return html


# Global converter instance (singleton pattern)
_default_converter: MarkdownConverter | None = None


def get_default_converter() -> MarkdownConverter:
    """Get the default MarkdownConverter instance.

    This function implements a singleton pattern, creating a single
    MarkdownConverter instance with default settings that is reused
    across all calls. This is efficient for most use cases where
    the same converter configuration is needed repeatedly.

    Returns:
        The default MarkdownConverter instance with standard settings
        (tables, strikethrough, tasklists, HTML, linkify, and breaks enabled).
    """
    global _default_converter

    if _default_converter is None:
        _default_converter = MarkdownConverter()

    return _default_converter


def convert_markdown_to_html(markdown_content: str) -> str:
    """Convert markdown content to HTML using the default converter.

    This is a convenience function for one-off markdown-to-HTML conversions.
    It uses the default MarkdownConverter instance with standard settings.
    For custom configurations, create a MarkdownConverter instance directly.

    Args:
        markdown_content: The markdown string to convert.

    Returns:
        The converted HTML string, ready for Zotero's TinyMCE editor.

    Example:
        >>> from millefeuille.domain.markdown_converter import (
    ...     convert_markdown_to_html
    ... )
        >>>
        >>> markdown = "# Title\\n\\nContent with **bold** text"
        >>> html = convert_markdown_to_html(markdown)
        >>> print(html)
        '<h1>Title</h1>\\n<p>Content with <strong>bold</strong> text</p>'
    """
    return get_default_converter().convert(markdown_content)
