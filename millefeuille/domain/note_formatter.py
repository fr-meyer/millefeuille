"""
Note formatting utilities for converting PageContent to Zotero-ready NotePayload.

This module provides the NoteFormatter class which converts extracted page content
from OCR processing into markdown-formatted notes that can be directly inserted into
Zotero via the API. The formatter handles empty page filtering, markdown structure
generation, and uses OCR output as-is with images and tables already embedded inline.

The markdown format structure:
- Title as multiple H1 markdown headings (one per component separated by "-"):
  # Page {N} -
  # {filename} -
  # OCR
- Content as pure markdown from OCR (images/tables already embedded)

Empty pages (pages without extractable content) are automatically filtered out
during batch processing to avoid creating empty notes in Zotero.

Example usage:
    >>> from millefeuille.domain.models import PageContent
    >>> from millefeuille.domain.note_formatter import NoteFormatter
    >>>
    >>> # Single page formatting
    >>> page = PageContent(
    ...     page_number=1,
    ...     markdown="# Title\\nContent here",
    ...     has_content=True
    ... )
    >>> note = NoteFormatter.format_note(page, "document.pdf", "ITEM_KEY_123")
    >>> print(note.title)
    'Page 1 -\ndocument.pdf -\nOCR'
    >>>
    >>> # Batch processing with empty page filtering
    >>> pages = [
    ...     PageContent(page_number=1, markdown="Content", has_content=True),
    ...     PageContent(page_number=2, markdown="", has_content=False),  # Empty
    ...     PageContent(page_number=3, markdown="More content", has_content=True),
    ... ]
    >>> notes = NoteFormatter.format_batch(pages, "document.pdf", "ITEM_KEY_123")
    >>> len(notes)  # Empty page filtered out
    2
"""

import re
import warnings

from millefeuille.domain.config import ConfigError
from millefeuille.domain.models import (
    NotePayload,
    PageContent,
)
from millefeuille.domain.note_validator import NoteValidator
from millefeuille.domain.table_converter import convert_markdown_tables_to_csv

ALLOWED_EXTRACTION_MODES = frozenset({"all_at_once", "page_by_page"})


class NoteFormatter:
    """Stateless formatter for converting PageContent to Zotero NotePayload.

    This class provides static methods for formatting individual pages and
    batch processing multiple pages with automatic empty page filtering.
    All methods are stateless and can be called without instantiation.

    The formatter generates markdown notes and handles embedding of images and tables
    when present in the page content.
    """

    @staticmethod
    def is_empty_page(page_content: PageContent) -> bool:
        """Check if a page contains no extractable content.

        This method checks the has_content flag to determine if a page should
        be considered empty. Empty pages are typically blank pages, image-only
        pages without OCR text, or pages with content below the extraction threshold.

        Args:
            page_content: The PageContent object to check for emptiness.

        Returns:
            True if the page is empty (has_content is False), False otherwise.

        Example:
            >>> page_with_content = PageContent(
            ...     page_number=1,
            ...     markdown="# Content",
            ...     has_content=True
            ... )
            >>> NoteFormatter.is_empty_page(page_with_content)
            False
            >>>
            >>> empty_page = PageContent(
            ...     page_number=2,
            ...     markdown="",
            ...     has_content=False
            ... )
            >>> NoteFormatter.is_empty_page(empty_page)
            True
        """
        return not page_content.has_content

    @staticmethod
    def _images_already_embedded(markdown: str) -> bool:
        """Check if images are already embedded in markdown as data URIs.

        This helper method checks if the markdown already contains embedded images
        (e.g., `![img-id](data:image/...)`) to avoid duplicating them when formatting.

        Args:
            markdown: Markdown content to check for embedded images.

        Returns:
            True if markdown contains embedded image data URIs, False otherwise.
        """
        # Check for data URI pattern in image markdown syntax
        pattern = r"!\[[^\]]*\]\(data:image/[^\)]+\)"
        return bool(re.search(pattern, markdown))

    @staticmethod
    def _tables_already_embedded(
        markdown: str, tables: list[str] | None = None
    ) -> bool:
        """Check if tables are already embedded in markdown.

        This helper method checks if the markdown contains table placeholders
        (e.g., `[tbl-3.md](tbl-3.md)`). If placeholders are found, tables are not
        yet embedded. If no placeholders are found, it checks if table content
        appears in the markdown to determine if tables are embedded.

        Args:
            markdown: Markdown content to check for table placeholders.
            tables: Optional list of table strings to check if they appear in markdown.

        Returns:
            True if tables are embedded (no placeholders and table content in markdown),
            False if placeholders found or tables not in markdown.
        """
        # Check for table placeholder pattern:
        # [tbl-X](tbl-X), [tbl-X.md](tbl-X.md), or [tbl-X.html](tbl-X.html)
        placeholder_pattern = (
            r"\[tbl-\d+(?:\.(?:md|html))?\]\(tbl-\d+(?:\.(?:md|html))?\)"
        )
        if re.search(placeholder_pattern, markdown):
            # Placeholders found, tables not embedded
            return False

        # No placeholders found - check if table content is in markdown
        # If tables list provided, check if any table content appears in markdown
        if tables:
            for table in tables:
                if isinstance(table, str) and table.strip():
                    # Check if table content (at least a partial match) appears
                    # in markdown
                    # Look for table row pattern: | Col1 | Col2 |
                    table_rows = [
                        line.strip() for line in table.split("\n") if "|" in line
                    ]
                    if table_rows:
                        # Check if first table row appears in markdown
                        first_row = table_rows[0]
                        if first_row in markdown:
                            return True

        # If no placeholders and no table content detected, assume not embedded
        # (backward compatibility: tables provided separately)
        return False

    @staticmethod
    def format_note(
        page_content: PageContent,
        pdf_filename: str,
        parent_item_key: str,
    ) -> NotePayload:
        """Format a single page content into a Zotero note payload.

        This method converts a PageContent object into a NotePayload ready for
        Zotero API insertion. The note title follows a multi-line format:
        "Page {page_number} -\n{pdf_filename} -\nOCR".

        The markdown content structure is:
        - Multiple H1 markdown headings (one per component separated by "-"):
          # Page {page_number} -
          # {pdf_filename} -
          # OCR
        - Blank line
        - Main markdown content
        - Tables appended when present (only if not already embedded)
        - Images appended when present (only if not already embedded)

        Note: If images are already embedded in the markdown (inline as data URIs),
        they are not appended separately to avoid duplication. Similarly, if tables
        are already embedded in the markdown (placeholders replaced with table content),
        they are not appended separately. This matches the reference implementation
        where images and tables are embedded inline.

        Note: This method does NOT filter empty pages. The caller is responsible
        for checking page content before calling this method, or use format_batch()
        which automatically filters empty pages.

        Args:
            page_content: The PageContent object to format into a note.
            pdf_filename: The source PDF filename for identification.
            parent_item_key: The Zotero item key that will be the parent of this note.

        Returns:
            A NotePayload object with all fields populated and ready for Zotero API.

        Example:
            >>> page = PageContent(
            ...     page_number=1,
            ...     markdown="# Title\\n![img-0.jpeg](data:image/jpeg;base64,...)",
            ...     has_content=True,
            ...     images=["base64_image_data"],
            ... )
            >>> note = NoteFormatter.format_note(page, "document.pdf", "ITEM_KEY_123")
            >>> # Images already in markdown are not duplicated
        """
        # Generate note title (multi-line format for API compatibility)
        title = f"Page {page_content.page_number} -\n{pdf_filename} -\nOCR"

        # Build markdown content structure with multiple H1 headings
        markdown_heading = (
            f"# Page {page_content.page_number} -\n# {pdf_filename} -\n# OCR"
        )
        markdown_parts = [markdown_heading, "\n\n"]

        # Add main markdown content
        markdown_parts.append(page_content.markdown)

        # Check if images and tables are already embedded in markdown
        images_embedded = NoteFormatter._images_already_embedded(page_content.markdown)
        tables_embedded = NoteFormatter._tables_already_embedded(
            page_content.markdown, page_content.tables
        )

        # Handle tables if present and NOT already embedded in markdown
        # This maintains backward compatibility while avoiding duplication
        if page_content.tables and not tables_embedded:
            markdown_parts.append("\n\n<!-- Tables -->\n")
            for table in page_content.tables:
                # Ensure table is a string (safety check in case conversion
                # failed upstream)
                if isinstance(table, str):
                    markdown_parts.append(table)
                else:
                    # Convert non-string table to string (defensive programming)
                    markdown_parts.append(str(table))
                markdown_parts.append("\n")

        # Handle images if present and NOT already embedded in markdown
        # This maintains backward compatibility while avoiding duplication
        if page_content.images and not images_embedded:
            markdown_parts.append("\n\n<!-- Images -->\n")
            for image_base64 in page_content.images:
                # Embed image as base64 data URI in HTML img tag
                # Use raw base64 or data URI depending on format
                if isinstance(image_base64, str) and image_base64.startswith("data:"):
                    src_uri = image_base64
                else:
                    src_uri = f"data:image/png;base64,{image_base64}"
                markdown_parts.append(
                    f'<img src="{src_uri}" '
                    f'alt="Page {page_content.page_number} image" />\n'
                )

        # Combine all parts into final markdown content
        content = "".join(markdown_parts)

        # Convert markdown tables to CSV fenced code blocks
        content = convert_markdown_tables_to_csv(content)

        # Create and return NotePayload
        return NotePayload(
            parent_item_key=parent_item_key,
            pdf_filename=pdf_filename,
            page_number=page_content.page_number,
            content=content,
            title=title,
        )

    @staticmethod
    def _format_all_pages_at_once(
        pages: list[PageContent],
        parent_item_key: str,
        pdf_filename: str,
        auto_split: bool = True,
        size_threshold: int = 256000,
        skip_empty_pages: bool = True,
    ) -> list[NotePayload]:
        """Format all pages into a single note (or multiple notes if split needed).

        Concatenates all page content with page separators. Respects size limits
        and auto-splitting if enabled.

        Args:
            pages: List of PageContent objects
            parent_item_key: Zotero item key
            pdf_filename: PDF filename
            auto_split: Whether to split oversized content
            size_threshold: Size threshold for splitting
            skip_empty_pages: Whether to filter out empty pages. If True, only pages
                with has_content=True are included. If False, all pages are included.

        Returns:
            List of NotePayload objects (typically 1, or more if split occurred)
        """
        # Conditionally filter empty pages based on skip_empty_pages config
        valid_pages = [p for p in pages if p.has_content] if skip_empty_pages else pages

        if not valid_pages:
            return []

        # Build combined content
        content_parts = [f"# {pdf_filename} -\n# OCR (All Pages)\n\n"]

        # Add each page with separator
        for page in valid_pages:
            content_parts.append(f"--- Page {page.page_number} ---\n\n")
            content_parts.append(page.markdown)
            content_parts.append("\n\n")

        combined_content = "".join(content_parts)

        # Collect and append non-embedded images and tables from all pages
        # Iterate through valid_pages to collect all images and tables
        for page in valid_pages:
            # Check if images/tables are embedded using helper methods
            images_embedded = NoteFormatter._images_already_embedded(page.markdown)
            tables_embedded = NoteFormatter._tables_already_embedded(
                page.markdown, page.tables
            )

            # Append non-embedded tables
            if page.tables and not tables_embedded:
                combined_content += "\n\n<!-- Tables -->\n"
                for table in page.tables:
                    if isinstance(table, str):
                        combined_content += table
                    else:
                        combined_content += str(table)
                    combined_content += "\n"

            # Append non-embedded images
            if page.images and not images_embedded:
                combined_content += "\n\n<!-- Images -->\n"
                for image_base64 in page.images:
                    if isinstance(image_base64, str) and image_base64.startswith(
                        "data:"
                    ):
                        src_uri = image_base64
                    else:
                        src_uri = f"data:image/png;base64,{image_base64}"
                    combined_content += (
                        f'<img src="{src_uri}" alt="Page {page.page_number} image" />\n'
                    )

        # Convert markdown tables to CSV
        combined_content = convert_markdown_tables_to_csv(combined_content)

        # Apply auto-splitting if enabled
        chunks = NoteValidator.validate_and_split(
            content=combined_content,
            threshold=size_threshold,
            allow_split=auto_split,
            filename=pdf_filename,
            page_number=None,  # Not applicable for all-at-once mode
            element_type="document",
        )

        # Create NotePayload objects
        result = []
        if len(chunks) == 1:
            # Single note (no split)
            payload = NotePayload(
                parent_item_key=parent_item_key,
                pdf_filename=pdf_filename,
                page_number=None,  # Not applicable for all-at-once mode
                content=chunks[0],
                title=f"{pdf_filename} -\nOCR (All Pages)",
            )
            result.append(payload)
        else:
            # Multiple notes (split occurred)
            total_parts = len(chunks)
            for part_num, chunk in enumerate(chunks, start=1):
                split_title = (
                    f"{pdf_filename} (Part {part_num} of {total_parts}) -"
                    f"\nOCR (All Pages)"
                )
                payload = NotePayload(
                    parent_item_key=parent_item_key,
                    pdf_filename=pdf_filename,
                    page_number=None,
                    content=chunk,
                    title=split_title,
                )
                result.append(payload)

        return result

    @staticmethod
    def format_main_notes(
        pages: list[PageContent],
        parent_item_key: str,
        pdf_filename: str,
        auto_split: bool = True,
        size_threshold: int = 256000,
        extraction_mode: str = "all_at_once",  # NEW PARAMETER
        skip_empty_pages: bool = True,
    ) -> list[NotePayload]:
        """Create page notes with auto-splitting support.

        Processes PageContent objects directly from OCR output and converts them
        into NotePayload objects. Content organization depends on extraction_mode:
        - 'all_at_once': Single note with all pages concatenated (default)
        - 'page_by_page': One note per page (legacy behavior)

        Args:
            pages: List of PageContent objects to format into notes. Empty pages
                (has_content=False) are filtered out if skip_empty_pages is True.
            parent_item_key: The Zotero item key that will be the parent of all notes.
            pdf_filename: The source PDF filename for identification.
            auto_split: If True, automatically split oversized content into multiple
                notes. If False, raises NoteSizeExceededError for oversized content.
                Defaults to True.
            size_threshold: Maximum content size in bytes before splitting is triggered.
                Defaults to 256000 (256KB).
            extraction_mode: How to organize pages into notes.
                - 'all_at_once': Single note with all pages (default)
                - 'page_by_page': One note per page (legacy)
            skip_empty_pages: Whether to filter out empty pages. If True, only pages
                with has_content=True are included. If False, all pages are included.
                Defaults to True.

        Returns:
            A list of NotePayload objects. May contain multiple payloads if
            splitting occurred. Empty pages are filtered out if
            skip_empty_pages is True.

        Raises:
            ConfigError: When extraction_mode is not one of
                ``all_at_once`` or ``page_by_page``.
            NoteSizeExceededError: When content exceeds threshold and
                auto_split is False.

        Example:
            >>> from millefeuille.domain.models import PageContent
            >>> page = PageContent(
            ...     page_number=1,
            ...     markdown="# Title\n\nContent with embedded images",
            ...     has_content=True
            ... )
            >>> notes = NoteFormatter.format_main_notes(
            ...     [page], "ITEM_KEY_123", "document.pdf"
            ... )
            >>> len(notes)
            1
            >>>
            >>> # With auto-splitting for large content
            >>> large_content = "# Header\n\n" + "x" * 300000
            >>> large_page = PageContent(
            ...     page_number=1,
            ...     markdown=large_content,
            ...     has_content=True
            ... )
            >>> notes = NoteFormatter.format_main_notes(
            ...     [large_page], "ITEM_KEY_123", "document.pdf", auto_split=True
            ... )
            >>> len(notes) > 1  # Split into multiple notes
            True
        """
        if extraction_mode not in ALLOWED_EXTRACTION_MODES:
            raise ConfigError(
                f"extraction_mode must be one of {sorted(ALLOWED_EXTRACTION_MODES)}, "
                f"got {extraction_mode!r}"
            )
        if extraction_mode == "all_at_once":
            return NoteFormatter._format_all_pages_at_once(
                pages,
                parent_item_key,
                pdf_filename,
                auto_split,
                size_threshold,
                skip_empty_pages,
            )
        # extraction_mode == "page_by_page"
        result = []

        # Process each page
        for page in pages:
            # Conditionally skip empty pages based on skip_empty_pages config
            if skip_empty_pages and not page.has_content:
                continue

            # Build initial note content (multi-line format)
            base_title = f"Page {page.page_number} -\n{pdf_filename} -\nOCR"
            markdown_heading = f"# Page {page.page_number} -\n# {pdf_filename} -\n# OCR"
            content_parts = [
                markdown_heading,
                "\n\n",
                page.markdown,  # Use markdown directly from OCR
            ]
            initial_content = "".join(content_parts)

            # Check if images/tables are embedded and append non-embedded media
            images_embedded = NoteFormatter._images_already_embedded(page.markdown)
            tables_embedded = NoteFormatter._tables_already_embedded(
                page.markdown, page.tables
            )

            # Append non-embedded tables
            if page.tables and not tables_embedded:
                content_parts.append("\n\n<!-- Tables -->\n")
                for table in page.tables:
                    if isinstance(table, str):
                        content_parts.append(table)
                    else:
                        content_parts.append(str(table))
                    content_parts.append("\n")

            # Append non-embedded images
            if page.images and not images_embedded:
                content_parts.append("\n\n<!-- Images -->\n")
                for image_base64 in page.images:
                    if isinstance(image_base64, str) and image_base64.startswith(
                        "data:"
                    ):
                        src_uri = image_base64
                    else:
                        src_uri = f"data:image/png;base64,{image_base64}"
                    content_parts.append(
                        f'<img src="{src_uri}" alt="Page {page.page_number} image" />\n'
                    )

            # Rebuild initial_content with appended media
            initial_content = "".join(content_parts)

            # Convert markdown tables to CSV fenced code blocks
            initial_content = convert_markdown_tables_to_csv(initial_content)

            # Apply auto-splitting if enabled
            chunks = NoteValidator.validate_and_split(
                content=initial_content,
                threshold=size_threshold,
                allow_split=auto_split,
                filename=pdf_filename,
                page_number=page.page_number,
                element_type="page",
            )

            # Create NotePayload objects for each chunk
            if len(chunks) == 1:
                # Single chunk (no split)
                payload = NotePayload(
                    parent_item_key=parent_item_key,
                    pdf_filename=pdf_filename,
                    page_number=page.page_number,
                    content=chunks[0],
                    title=base_title,
                )
                result.append(payload)
            else:
                # Multiple chunks (split occurred)
                total_parts = len(chunks)
                for part_num, chunk in enumerate(chunks, start=1):
                    # Generate split title including PDF filename for clarity
                    part_label = f"(Part {part_num} of {total_parts})"
                    split_title = (
                        f"Page {page.page_number} {part_label} -\n{pdf_filename} -\nOCR"
                    )
                    split_heading = (
                        f"# Page {page.page_number} {part_label} -\n"
                        f"# {pdf_filename} -\n# OCR"
                    )

                    # Prepend split title heading before existing chunk content
                    # Optionally remove the initial base heading if it
                    # matches the original pattern
                    escaped_filename = re.escape(pdf_filename)
                    base_heading_pattern = (
                        rf"^#\s+Page\s+{page.page_number}\s+-\s*\n"
                        rf"#\s+{escaped_filename}\s+-\s*\n"
                        rf"#\s+OCR"
                    )

                    # Check if chunk starts with the base page heading
                    base_heading_match = re.search(
                        base_heading_pattern,
                        chunk,
                        re.IGNORECASE | re.MULTILINE | re.DOTALL,
                    )
                    if base_heading_match:
                        # Remove the base page heading (multi-line H1 format)
                        # and any following blank lines. This preserves all
                        # section headers (e.g., ## Header 2) in the chunk
                        chunk_without_base_heading = re.sub(
                            rf"^#\s+Page\s+{page.page_number}\s+-\s*\n"
                            rf"#\s+{re.escape(pdf_filename)}\s+-\s*\n"
                            rf"#\s+OCR.*?\n\s*",
                            "",
                            chunk,
                            count=1,
                            flags=re.MULTILINE | re.DOTALL,
                        )
                        # Prepend split title heading
                        chunk_with_updated_heading = (
                            f"{split_heading}\n\n{chunk_without_base_heading}"
                        )
                    else:
                        # Chunk doesn't start with base page heading, prepend
                        # split title heading. This preserves all existing
                        # headers (including section headers)
                        chunk_with_updated_heading = f"{split_heading}\n\n{chunk}"

                    payload = NotePayload(
                        parent_item_key=parent_item_key,
                        pdf_filename=pdf_filename,
                        page_number=page.page_number,
                        content=chunk_with_updated_heading,
                        title=split_title,
                    )
                    result.append(payload)

        return result

    @staticmethod
    def format_batch(
        page_contents: list[PageContent],
        pdf_filename: str,
        parent_item_key: str,
        skip_empty_pages: bool = True,
    ) -> list[NotePayload]:
        """Format multiple pages into Zotero note payloads with optional
        empty page filtering.

        .. deprecated:: [version]
            This method is deprecated. Use `format_main_notes()` for more flexible
            note creation with figure/table linking and auto-splitting support.

        This method processes a list of PageContent objects and converts them
        into NotePayload objects. Empty pages (pages without extractable
        content) can be optionally filtered out before formatting based on
        the skip_empty_pages parameter.

        The method maintains page numbering from the original PageContent objects,
        so page numbers in the resulting notes will match the source page numbers
        (which are per-PDF, not global across all PDFs).

        Args:
            page_contents: List of PageContent objects to format into notes.
            pdf_filename: The source PDF filename for identification.
            parent_item_key: The Zotero item key that will be the parent of all notes.
            skip_empty_pages: If True, filter out empty pages before formatting.
                If False, format all pages regardless of content. Defaults to True.

        Returns:
            A list of NotePayload objects. If skip_empty_pages is True, empty pages
            are filtered out. If False, all pages are formatted. Returns an empty
            list if input list is empty.

        Example:
            >>> pages = [
            ...     PageContent(page_number=1, markdown="Content", has_content=True),
            ...     PageContent(page_number=2, markdown="", has_content=False),  # Empty
            ...     PageContent(page_number=3, markdown="More", has_content=True),
            ...     PageContent(page_number=4, markdown="", has_content=False),  # Empty
            ... ]
            >>> notes = NoteFormatter.format_batch(
            ...     pages, "document.pdf", "ITEM_KEY_123", skip_empty_pages=True
            ... )
            >>> len(notes)  # Two empty pages filtered out
            2
            >>> notes = NoteFormatter.format_batch(
            ...     pages, "document.pdf", "ITEM_KEY_123", skip_empty_pages=False
            ... )
            >>> len(notes)  # All pages included
            4
        """
        warnings.warn(
            "format_batch() is deprecated. Use format_main_notes() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Conditionally filter out empty pages based on skip_empty_pages config
        if skip_empty_pages:
            valid_pages = [
                page for page in page_contents if not NoteFormatter.is_empty_page(page)
            ]
        else:
            valid_pages = page_contents

        # Format each valid page into a note
        notes = [
            NoteFormatter.format_note(page, pdf_filename, parent_item_key)
            for page in valid_pages
        ]

        return notes
