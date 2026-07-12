"""
Note validation utilities for checking content size and splitting oversized notes.

This module provides the NoteValidator class which validates note content size
against Zotero's maximum note size limit (typically 250KB) and handles splitting
of oversized content into smaller chunks. The validator ensures that notes created
for Zotero do not exceed size limits, preventing API failures.

The validation process:
- Checks content size against configurable threshold (default 256,000 bytes)
- Returns single-item list for content under threshold
- Raises NoteSizeExceededError for oversized content when splitting is disabled
- Delegates to smart splitting algorithm for oversized content using three-tier strategy

Integration points:
- Will be called by NoteFormatter before creating notes
- Will be used in processor to validate content before batch creation
- Context parameters should be populated from PageContent and NotePayload objects

Example usage:
    >>> from millefeuille.domain.note_validator import NoteValidator
    >>>
    >>> # Content under threshold
    >>> content = "Small note content"
    >>> chunks = NoteValidator.validate_and_split(content)
    >>> len(chunks)
    1
    >>>
    >>> # Oversized content with splitting disabled
    >>> large_content = "x" * 300000
    >>> try:
    ...     NoteValidator.validate_and_split(large_content, allow_split=False)
    ... except NoteSizeExceededError as e:
    ...     print(f"Error: {e.actual_size} bytes exceeds {e.threshold} bytes")
    >>>
    >>> # Oversized content with splitting enabled
    >>> large_content = (
    ...     "# Header 1\n\n" + "x" * 200000 + "\n\n## Header 2\n\n" + "y" * 200000
    ... )
    >>> chunks = NoteValidator.validate_and_split(large_content)
    >>> len(chunks) > 1
    True
    >>> all(len(chunk) <= 256000 for chunk in chunks)
    True
"""

import re

from millefeuille.clients.exceptions import NoteSizeExceededError

# Default threshold: 256,000 characters for Zotero compatibility
# Using 256KB character count instead of 250KB to provide a small safety margin
# Note: ProcessingConfig.note_size_threshold (default 180000) overrides this value
# in production. The 256000 threshold here provides a safe upper bound for validation.
DEFAULT_NOTE_SIZE_THRESHOLD = 256000


class NoteValidator:
    """Stateless validator for note content size validation and splitting.

    This class provides static methods for validating note content size against
    Zotero's maximum note size limit and handling splitting of oversized content
    into smaller chunks. All methods are stateless and can be called without
    instantiation.

    The validator checks content size against a configurable threshold (default
    256,000 characters, which is 250KB with a small safety margin for Zotero
    compatibility). Content that exceeds the threshold can either raise an error
    (when splitting is disabled) or be split into smaller chunks (when splitting
    is enabled).

    The smart splitting algorithm uses a three-tier strategy:
    1. Header-based splitting: Attempts to split at markdown headers (#, ##, ###, etc.)
    2. Paragraph-based splitting: Falls back to splitting at paragraph
        boundaries (\\n\\n)
    3. Character-based splitting: Final fallback with space-aware word breaking

    Example:
        >>> # Validate content under threshold
        >>> content = "Small note"
        >>> chunks = NoteValidator.validate_and_split(content)
        >>> chunks
        ['Small note']
        >>>
        >>> # Oversized content with splitting disabled
        >>> large = "x" * 300000
        >>> NoteValidator.validate_and_split(large, allow_split=False)
        Traceback (most recent call last):
        ...
        NoteSizeExceededError: Note content exceeds size limit...
        >>>
        >>> # Oversized content with splitting enabled
        >>> large = (
        ...     "# Section 1\n\n" + "x" * 200000 + "\n\n## Section 2\n\n"
        ...     + "y" * 200000
        ... )
        >>> chunks = NoteValidator.validate_and_split(large)
        >>> len(chunks) > 1
        True
    """

    @staticmethod
    def validate_and_split(
        content: str,
        threshold: int = DEFAULT_NOTE_SIZE_THRESHOLD,
        allow_split: bool = True,
        filename: str | None = None,
        page_number: int | None = None,
        element_type: str | None = None,
    ) -> list[str]:
        """Validate note content size and split if necessary.

            This method checks the size of note content against a
            configurable threshold.
            If content is under or equal to the threshold, it returns a single-item list
            containing the original content. If content exceeds the
            threshold and splitting
            is disabled, it raises NoteSizeExceededError with full context. If content
            exceeds the threshold and splitting is enabled, it delegates to the smart
            splitting algorithm using a three-tier strategy.

            Args:
                content: The note content string to validate and potentially split.
                threshold: Maximum allowed content size in character count.
                    Defaults to DEFAULT_NOTE_SIZE_THRESHOLD (256,000 characters).
                allow_split: If True, attempt to split oversized content into smaller
                    chunks. If False, raise NoteSizeExceededError for oversized content.
                    Defaults to True.
                filename: Optional filename for error context. Should be provided when
                    validating content from a specific file.
                page_number: Optional page number for error context. Should be provided
                    when validating content from a specific page.
                element_type: Optional element type for error context (e.g., "figure",
                    "table"). Should be provided when validating specific element types.

            Returns:
                A list of strings containing one or more content chunks. If content is
                under threshold, returns a single-item list with the original content.
                If content exceeds threshold and splitting is enabled, returns multiple
                chunks split using the three-tier strategy.

            Raises:
                NoteSizeExceededError: When content exceeds threshold and allow_split
                    is False. The exception includes filename,
                    page_number, element_type,
                    actual_size, and threshold for debugging.

            Example:
                >>> # Content under threshold
                >>> content = "Small note content"
                >>> chunks = NoteValidator.validate_and_split(content)
                >>> chunks
                ['Small note content']
                >>>
                >>> # Oversized content with splitting disabled
                >>> large_content = "x" * 300000
                >>> try:
                ...     NoteValidator.validate_and_split(
                ...         large_content,
                ...         allow_split=False,
                ...         filename="document.pdf",
                ...         page_number=1,
                ...         element_type="figure"
                ...     )
                ... except NoteSizeExceededError as e:
                ...     print(f"Size: {e.actual_size}, Threshold: {e.threshold}")
                >>>
                >>> # Oversized content with splitting enabled
                >>> large_content = (
        ...     "# Header 1\n\n" + "x" * 200000 + "\n\n## Header 2\n\n" + "y" * 200000
        ... )
                >>> chunks = NoteValidator.validate_and_split(large_content)
                >>> len(chunks) > 1
                True
                >>> all(len(chunk) <= 256000 for chunk in chunks)
                True
        """
        # Step 1: Size Check
        content_size = len(content)

        if content_size <= threshold:
            return [content]

        # Step 2: Handle Oversized Content
        if not allow_split:
            # Validate context for better error messages
            NoteValidator._validate_context(filename, page_number, element_type)

            # Raise exception with all context parameters
            raise NoteSizeExceededError(
                message="Note content exceeds size limit and cannot be split",
                filename=filename or "unknown",
                page_number=page_number or 0,
                element_type=element_type or "note",
                actual_size=content_size,
                threshold=threshold,
            )

        # Step 3: Delegate to Splitting Logic
        # Smart splitting algorithm uses three-tier strategy
        return NoteValidator._split_content(
            content, threshold, filename, page_number, element_type
        )

    @staticmethod
    def _split_content(
        content: str,
        threshold: int,
        filename: str | None = None,
        page_number: int | None = None,
        element_type: str | None = None,
    ) -> list[str]:
        """Split oversized content into smaller chunks using smart algorithm.

        This method implements a three-tier splitting strategy:
        1. Split at markdown headers (#, ##, ###, etc.) when possible
        2. Split at paragraph boundaries (\\n\\n) when headers not available
        3. Split at character boundaries with space-aware breaking as fallback

        The method attempts each strategy in order, falling back to the next if
        the current strategy fails (e.g., if any chunk would exceed threshold).
        Character-based splitting always succeeds as the final fallback.

        Args:
            content: The content string to split into smaller chunks.
            threshold: Maximum size for each chunk in character count.
            filename: Optional filename for error context.
            page_number: Optional page number for error context.
            element_type: Optional element type for error context.

        Returns:
            A list of strings, each under the threshold size. All chunks are
            validated to ensure they do not exceed the threshold.

        Example:
            >>> content = (
            ...     "# Header 1\\n\\n" + "x" * 200000 + "\\n\\n## Header 2\\n\\n"
            ...     + "y" * 200000
            ... )
            >>> chunks = NoteValidator._split_content(content, threshold=256000)
            >>> len(chunks) > 1
            True
            >>> all(len(chunk) <= 256000 for chunk in chunks)
            True
        """
        # Try header-based splitting first (most semantic)
        chunks = NoteValidator._split_by_headers(content, threshold)
        if chunks:
            NoteValidator._validate_chunks(chunks, threshold)
            return chunks

        # Fall back to paragraph-based splitting
        chunks = NoteValidator._split_by_paragraphs(content, threshold)
        if chunks:
            NoteValidator._validate_chunks(chunks, threshold)
            return chunks

        # Final fallback: character-based splitting (always succeeds)
        chunks = NoteValidator._split_by_characters(
            content, threshold, filename, page_number, element_type
        )
        NoteValidator._validate_chunks(chunks, threshold)
        return chunks

    @staticmethod
    def _split_by_headers(content: str, threshold: int) -> list[str]:
        """Split content at markdown header boundaries.

        This method attempts to split content at markdown headers (#, ##, ###, etc.)
        found at the start of lines. Headers are identified using the regex pattern
        `^#{1,6}\\s+` which matches 1-6 hash symbols followed by whitespace at the
        beginning of a line.

        The method splits content at header boundaries while preserving headers in
        their respective chunks. If any resulting chunk would exceed the threshold,
        this strategy fails and returns an empty list, indicating that the next
        strategy should be tried.

        Edge cases handled:
        - No headers found: Returns empty list
        - Headers at start/end of content: Handled correctly
        - Consecutive headers: Creates chunks with minimal content between headers
        - Single section exceeding threshold: Returns empty list to trigger fallback

        Args:
            content: The content string to split.
            threshold: Maximum size for each chunk in character count.

        Returns:
            A list of strings split at header boundaries, or empty list if this
            strategy fails (e.g., any chunk would exceed threshold).

        Example:
            >>> content = "# Header 1\\n\\nText 1\\n\\n## Header 2\\n\\nText 2"
            >>> chunks = NoteValidator._split_by_headers(content, threshold=100)
            >>> len(chunks) >= 2
            True
        """
        # Regex pattern to match markdown headers at line start: #, ##, ###, etc.
        # Pattern: ^#{1,6}\\s+ matches 1-6 hash symbols followed by whitespace
        header_pattern = r"^#{1,6}\s+"

        # Find all header positions (line start indices)
        lines = content.split("\n")
        header_positions = []
        current_pos = 0

        for _, line in enumerate(lines):
            if re.match(header_pattern, line):
                header_positions.append(current_pos)
            current_pos += len(line) + 1  # +1 for newline character

        # If no headers found, this strategy fails
        if not header_positions:
            return []

        # Split content at header boundaries
        chunks = []
        for i in range(len(header_positions)):
            start = header_positions[i]
            # End is next header position or end of content
            end = (
                header_positions[i + 1]
                if i + 1 < len(header_positions)
                else len(content)
            )
            chunk = content[start:end]

            # If any chunk exceeds threshold, this strategy fails
            if len(chunk) > threshold:
                return []

            chunks.append(chunk)

        # If we have content before first header, include it in first chunk
        if header_positions[0] > 0:
            prefix = content[: header_positions[0]]
            if len(prefix) > threshold:
                return []  # Prefix too large, strategy fails
            if chunks:
                chunks[0] = prefix + chunks[0]
                # Re-check first chunk after combining
                if len(chunks[0]) > threshold:
                    return []
            else:
                chunks.insert(0, prefix)

        return chunks if chunks else []

    @staticmethod
    def _split_by_paragraphs(content: str, threshold: int) -> list[str]:
        """Split content at paragraph boundaries (double newlines).

        This method splits content by double newlines (\\n\\n) to identify paragraph
        boundaries. It uses a greedy algorithm to combine paragraphs into chunks
        while staying under the threshold: paragraphs are added to the current chunk
        until adding the next paragraph would exceed the threshold, at which point
        a new chunk is started.

        Edge cases handled:
        - No paragraph breaks: Returns empty list to trigger character-based fallback
        - Very large single paragraph: Returns empty list if exceeds threshold
        - Trailing newlines: Preserved in chunks
        - Empty paragraphs: Handled correctly

        Args:
            content: The content string to split.
            threshold: Maximum size for each chunk in character count.

        Returns:
            A list of strings split at paragraph boundaries, or empty list if
            this strategy fails (e.g., no paragraph breaks or single paragraph
            exceeds threshold).

        Example:
            >>> content = "Para 1\\n\\nPara 2\\n\\nPara 3"
            >>> chunks = NoteValidator._split_by_paragraphs(content, threshold=20)
            >>> len(chunks) >= 2
            True
        """
        # Split by double newlines to identify paragraphs
        paragraphs = content.split("\n\n")

        # If no paragraph breaks found, this strategy fails
        if len(paragraphs) <= 1:
            # Check if single paragraph exceeds threshold
            if len(content) > threshold:
                return []
            return [content]

        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            para_size = len(para)
            # Account for double newline separator when combining
            separator_size = 2 if current_chunk else 0

            # If adding this paragraph would exceed threshold, start new chunk
            if current_size + separator_size + para_size > threshold:
                # If current chunk is empty and paragraph itself exceeds threshold,
                # this strategy fails (need character-based splitting)
                if not current_chunk and para_size > threshold:
                    return []

                # Save current chunk and start new one
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_size = para_size
            else:
                # Add paragraph to current chunk
                current_chunk.append(para)
                current_size += separator_size + para_size

        # Add final chunk
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks if chunks else []

    @staticmethod
    def _split_by_characters(
        content: str,
        threshold: int,
        filename: str | None = None,
        page_number: int | None = None,
        element_type: str | None = None,
    ) -> list[str]:
        """Split content into character-based chunks with space-aware breaking.

        This method splits content into chunks of maximum `threshold` size, using
        space-aware breaking to avoid splitting words. When splitting mid-chunk, it
        looks backward for the last space within a reasonable range (last 100
        characters)
        to break at. If no space is found in that range, it performs a hard break
        at the threshold boundary.

        This is the final fallback strategy and always succeeds, even if it means
        splitting words or creating many small chunks.

        Edge cases handled:
        - No spaces in content: Hard break at threshold boundary
        - Very long words: Hard break if word exceeds threshold
        - Content exactly at threshold: Returns single chunk
        - Unicode characters: Uses len() for character count (already established)
        - Non-positive threshold: Raises ValueError before processing

        Args:
            content: The content string to split.
            threshold: Maximum size for each chunk in character count.
                Must be positive.
            filename: Optional filename for error context.
            page_number: Optional page number for error context.
            element_type: Optional element type for error context.

        Returns:
            A list of strings, each under or equal to the threshold size. This method
            always succeeds and returns at least one chunk.

        Raises:
            ValueError: If threshold is zero or negative.

        Example:
            >>> content = "x" * 300000
            >>> chunks = NoteValidator._split_by_characters(content, threshold=256000)
            >>> len(chunks) >= 2
            True
            >>> all(len(chunk) <= 256000 for chunk in chunks)
            True
        """
        # Guard against non-positive threshold to prevent infinite loop or regression
        if threshold <= 0:
            raise ValueError(
                f"Threshold must be positive, got {threshold}. "
                f"A non-positive threshold would cause the splitting loop to "
                f"hang or regress."
            )

        if len(content) <= threshold:
            return [content]

        # Check for data URIs before character-based splitting
        # Data URIs are long continuous base64 strings that cannot be split
        # without corruption
        if "data:image" in content or "data:" in content:
            # Calculate the longest continuous non-space run to detect base64 data
            longest_run = 0
            current_run = 0
            for char in content:
                if char != " " and char != "\n":
                    current_run += 1
                    longest_run = max(longest_run, current_run)
                else:
                    current_run = 0

            # If longest run exceeds threshold, it's likely an unsplittable
            # data URI
            if longest_run > threshold:
                # Validate context before raising error to ensure full context
                # is available
                NoteValidator._validate_context(filename, page_number, element_type)
                raise NoteSizeExceededError(
                    message=(
                        "Content contains unsplittable data URIs that exceed "
                        "size threshold. Character-based splitting would "
                        "corrupt base64 image data."
                    ),
                    filename=filename or "unknown",
                    page_number=page_number or 0,
                    element_type=element_type or "note",
                    actual_size=len(content),
                    threshold=threshold,
                )

        chunks = []
        start = 0
        # Lookback range for finding spaces (to avoid splitting words)
        # Ensure space_lookback is at least 1 to guarantee loop progression
        space_lookback = max(1, min(100, threshold // 4))  # Reasonable lookback range

        while start < len(content):
            end = start + threshold

            # If remaining content fits in one chunk, take it all
            if end >= len(content):
                chunks.append(content[start:])
                break

            # Try to find a space within lookback range for clean word breaking
            space_pos = -1
            lookback_start = max(start, end - space_lookback)
            for i in range(end - 1, lookback_start - 1, -1):
                if content[i] == " ":
                    space_pos = i + 1  # Break after the space
                    break

            # If space found, break at space; otherwise hard break at threshold
            chunk_end = space_pos if space_pos > start else end

            # Ensure chunk_end always advances start (defensive programming)
            # This guarantees the loop progresses even in edge cases
            if chunk_end <= start:
                # Fallback: force advancement by at least 1 character
                chunk_end = start + 1

            chunks.append(content[start:chunk_end])
            start = chunk_end

        return chunks if chunks else [content]

    @staticmethod
    def _validate_chunks(chunks: list[str], threshold: int) -> bool:
        """Validate that all chunks are under the threshold.

        This helper method provides defensive programming by verifying that all
        chunks returned by splitting strategies are under the threshold. This helps
        catch bugs in splitting logic and ensures correctness.

        Args:
            chunks: List of content chunks to validate.
            threshold: Maximum allowed size for each chunk in character count.

        Returns:
            True if all chunks are valid (under threshold).

        Raises:
            ValueError: If any chunk exceeds the threshold. This indicates an
                internal error in the splitting logic and should not happen in
                normal operation.

        Example:
            >>> chunks = ["chunk1", "chunk2"]
            >>> NoteValidator._validate_chunks(chunks, threshold=100)
            True
            >>> NoteValidator._validate_chunks(["x" * 200], threshold=100)
            Traceback (most recent call last):
            ...
            ValueError: ...
        """
        for i, chunk in enumerate(chunks):
            if len(chunk) > threshold:
                raise ValueError(
                    f"Internal error: Chunk {i} exceeds threshold "
                    f"({len(chunk)} > {threshold} characters). This indicates a bug "
                    f"in the splitting logic."
                )
        return True

    @staticmethod
    def _validate_context(
        filename: str | None,
        page_number: int | None,
        element_type: str | None,
    ) -> None:
        """Validate context parameters for error reporting.

        This helper method checks if context parameters are provided when raising
        exceptions. While context parameters are optional, having them improves
        error messages and debugging. This method logs warnings if context is
        incomplete (helps with debugging) but does not raise errors, as context
        is optional for basic validation scenarios.

        Args:
            filename: Optional filename for error context.
            page_number: Optional page number for error context.
            element_type: Optional element type for error context.

        Note:
            This method currently only validates that context is meaningful.
            In the future, it could log warnings when context is missing to help
            with debugging. For now, it's a placeholder for future enhancements.
        """
        # Context validation: ensure error messages are informative
        # If all context is None, error messages will be less helpful
        # This is acceptable for basic validation, but better context improves debugging
        # Future enhancement: could log warnings when context is incomplete
        pass
