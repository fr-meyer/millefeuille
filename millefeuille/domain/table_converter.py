"""
Markdown table to CSV conversion for Zotero note content.

This module provides utilities to convert markdown pipe tables into CSV format
wrapped in fenced code blocks. This prevents Notion API validation errors by
avoiding excessive rich_text array elements that occur when tables are parsed
as markdown structures.

The converter uses markdown-it-py with GFM-like preset to parse markdown and
identify table structures, then converts them to CSV format using Python's
csv module for proper escaping of special characters.

Large tables (CSV content exceeding 2000 characters) are automatically split
into multiple CSV blocks, each starting with the header row. This ensures
each CSV block stays under Notion's 2000-character limit for rich_text.text.content.

Example usage:
    >>> from millefeuille.domain.table_converter import (
    ...     convert_markdown_tables_to_csv
    ... )
    >>>
    >>> markdown = "| Col1 | Col2 |\\n|------|------|\\n| Val1 | Val2 |"
    >>> result = convert_markdown_tables_to_csv(markdown)
    >>> print(result)
    ```csv
    Col1,Col2
    Val1,Val2
    ```

    >>> # Large table automatically chunked into multiple CSV blocks
    >>> large_markdown = (
    ...     "| Col1 | Col2 |\\n|---|---|\\n"
    ...     + "\\n".join([f"| {i} | {i*2} |" for i in range(100)])
    ... )
    >>> result = convert_markdown_tables_to_csv(large_markdown)
    >>> # Result contains multiple ```csv blocks separated by double newlines
"""

import csv
import io
import logging

from markdown_it import MarkdownIt
from markdown_it.token import Token

logger = logging.getLogger(__name__)


def convert_markdown_tables_to_csv(
    markdown_content: str, chunk_size: int = 2000
) -> str:
    """Convert all Markdown pipe tables to CSV fenced code blocks.

    Parses markdown using MarkdownIt to identify table structures and converts
    them to CSV format wrapped in fenced code blocks. This prevents Notion API
    validation errors by avoiding excessive rich_text array elements.

    Large tables (CSV content > chunk_size characters) are automatically split
    into multiple CSV blocks, each starting with the header row. This ensures
    each CSV block stays under Notion's 2000-character limit for rich_text.text.content.

    Args:
        markdown_content: Markdown string potentially containing pipe tables.
        chunk_size: Maximum size for CSV chunks in characters. Defaults to 2000.
            Tables exceeding this size will be split into multiple CSV blocks.

    Returns:
        Markdown string with tables replaced by CSV code blocks. Returns
        original content unchanged if no tables found or if input is empty.
        Large tables may be split into multiple CSV blocks separated by double newlines.

    Example:
        >>> markdown = "| A | B |\\n|---|---|\\n| 1 | 2 |"
        >>> result = convert_markdown_tables_to_csv(markdown)
        >>> print(result)
        ```csv
        A,B
        1,2
        ```

        >>> # Large table automatically chunked
        >>> large_markdown = (
    ...     "| Col1 | Col2 |\\n|---|---|\\n"
    ...     + "\\n".join([f"| {i} | {i*2} |" for i in range(100)])
    ... )
        >>> result = convert_markdown_tables_to_csv(large_markdown, chunk_size=500)
        >>> csv_blocks = result.count("```csv")
        >>> csv_blocks >= 2  # Multiple chunks created
        True
    """
    # Check for empty/whitespace-only input
    if not markdown_content or not markdown_content.strip():
        return markdown_content

    # Initialize MarkdownIt parser with GFM-like preset
    md = MarkdownIt("gfm-like")
    md.enable(["table"])

    try:
        # Parse markdown to token stream
        tokens = md.parse(markdown_content)

        # Find all table regions
        table_regions = _find_table_regions(tokens)

        # If no tables found, return original markdown unchanged
        if not table_regions:
            return markdown_content

        # Build list of replacements with their positions
        # Process in forward order to calculate positions from original markdown
        lines = markdown_content.split("\n")
        replacements = []

        for (
            token_start_idx,
            token_end_idx,
            map_start_line,
            map_end_line,
        ) in table_regions:
            try:
                # Check if table is inside a code block (defensive check)
                if _is_inside_code_block(tokens, token_start_idx):
                    logger.debug(
                        f"Skipping table at line {map_start_line + 1}: "
                        f"inside code block"
                    )
                    continue

                # Extract rows from table tokens
                rows = _extract_table_rows(tokens, token_start_idx, token_end_idx)

                # Convert rows to CSV format
                csv_content = _tokens_to_csv(rows)

                # Extract header row for chunking
                header_row = rows[0] if rows else []

                # Chunk CSV if it exceeds chunk_size
                csv_chunks = _chunk_csv(csv_content, header_row, chunk_size=chunk_size)

                # Build replacement: single or multiple CSV fenced code blocks
                if len(csv_chunks) == 1:
                    # Single chunk: simple CSV block
                    replacement = f"```csv\n{csv_chunks[0]}\n```"
                else:
                    # Multiple chunks: join with double newlines
                    chunk_blocks = [f"```csv\n{chunk}\n```" for chunk in csv_chunks]
                    replacement = "\n\n".join(chunk_blocks)

                # Calculate character positions from line numbers in original markdown
                # map_start_line and map_end_line are 0-based
                # map[0] is start line (inclusive), map[1] is end line (exclusive)
                start_char_pos = sum(len(line) + 1 for line in lines[:map_start_line])

                # Calculate end position
                if map_end_line >= len(lines):
                    # map_end_line points past the end of document, use full
                    # document length
                    end_char_pos = len(markdown_content)
                else:
                    # Calculate up to map_end_line (exclusive)
                    # This gives us the position right after the last character
                    # of line (map_end_line - 1)
                    end_char_pos = sum(len(line) + 1 for line in lines[:map_end_line])

                replacements.append((start_char_pos, end_char_pos, replacement))

            except Exception as e:
                # Log warning for malformed tables, skip this table
                logger.warning(
                    f"Failed to convert table at lines "
                    f"{map_start_line + 1}-{map_end_line + 1}: {e}"
                )
                continue

        # Apply replacements in reverse order (end-to-start) to preserve positions
        result = markdown_content
        for start_pos, end_pos, replacement in reversed(replacements):
            result = result[:start_pos] + replacement + result[end_pos:]

        return result

    except Exception as e:
        # Log error for parsing failures, return original markdown
        logger.error(f"Failed to parse markdown for table conversion: {e}")
        return markdown_content


def _find_table_regions(tokens: list[Token]) -> list[tuple[int, int, int, int]]:
    """Find all table regions in the token stream.

    Traverses tokens to find table_open → table_close pairs and tracks their
    indices and map attributes (line ranges in source).

    Args:
        tokens: List of markdown tokens from MarkdownIt parser.

    Returns:
        List of tuples: (token_start_idx, token_end_idx, map_start_line, map_end_line).
        Returns empty list if no tables found.
    """
    regions = []
    i = 0

    while i < len(tokens):
        token = tokens[i]

        if token.type == "table_open":
            # Found start of table, find matching close
            table_start_idx = i
            # Use table_open map for both start and end lines
            # map[0] is start line (inclusive), map[1] is end line (exclusive)
            table_start_line = token.map[0] if token.map else 0
            table_end_line = token.map[1] if token.map else 0

            # Find matching table_close token
            depth = 1
            j = i + 1
            table_end_idx = None

            while j < len(tokens) and depth > 0:
                if tokens[j].type == "table_open":
                    depth += 1
                elif tokens[j].type == "table_close":
                    depth -= 1
                    if depth == 0:
                        table_end_idx = j
                        break
                j += 1

            if table_end_idx is not None:
                regions.append(
                    (table_start_idx, table_end_idx, table_start_line, table_end_line)
                )
                i = table_end_idx + 1
            else:
                # Unclosed table, skip
                i += 1
        else:
            i += 1

    return regions


def _is_inside_code_block(tokens: list[Token], table_start_idx: int) -> bool:
    """Check if a table is inside a code block.

    Checks if the table's source line falls within any code block token's map range.
    This is a defensive check since markdown-it already doesn't parse tables inside
    code blocks, but explicit verification improves code clarity and future-proofs
    against parser changes.

    Args:
        tokens: List of markdown tokens from MarkdownIt parser.
        table_start_idx: Index of the table_open token to check.

    Returns:
        True if table is inside a code block, False otherwise.

    Example:
        >>> # Table at index 0 is never inside a code block
        >>> _is_inside_code_block(tokens, 0)
        False
    """
    # Table at index 0 cannot be inside a code block
    if table_start_idx == 0:
        return False

    # Get the table's source line from its map attribute
    table_token = tokens[table_start_idx]
    if not table_token.map:
        return False

    map_start_line = table_token.map[0]

    # Traverse tokens from start to table_start_idx to find code blocks
    for i in range(table_start_idx):
        token = tokens[i]

        # Check for code block tokens (fence or code_block)
        if token.type in ("fence", "code_block") and token.map:
            # map[0] is start line (inclusive), map[1] is end line (exclusive)
            code_start = token.map[0]
            code_end = token.map[1]
            # If table's start line falls within this code block's range,
            # table is inside
            if code_start <= map_start_line < code_end:
                return True

    return False


def _extract_table_rows(
    tokens: list[Token], table_start_idx: int, table_end_idx: int
) -> list[list[str]]:
    """Extract table rows from table tokens.

    Traverses tokens between table_start and table_end to extract cell content
    and build a list of rows. Handles uneven rows by padding shorter rows with
    empty strings to match max row length.

    Args:
        tokens: List of markdown tokens from MarkdownIt parser.
        table_start_idx: Index of table_open token.
        table_end_idx: Index of table_close token.

    Returns:
        List of rows, where each row is a list of cell strings. Empty list if
        no rows found or if table structure is invalid.
    """
    rows: list[list[str]] = []
    current_row: list[str] = []

    # State tracking
    in_cell = False
    current_cell_tokens = []

    # Traverse tokens within table region
    for i in range(table_start_idx + 1, table_end_idx):
        token = tokens[i]

        if token.type in ("th_open", "td_open"):
            # Start of cell
            in_cell = True
            current_cell_tokens = []

        elif token.type == "inline":
            # Cell content (text) - inline token contains the cell content
            if in_cell:
                current_cell_tokens.append(token)

        elif token.type in ("th_close", "td_close"):
            # End of cell
            if in_cell:
                # Extract text from inline tokens
                cell_text = _extract_text_from_inline_tokens(current_cell_tokens)
                current_row.append(cell_text)
                in_cell = False
                current_cell_tokens = []

        elif token.type == "tr_close":
            # End of row
            if current_row:
                rows.append(current_row)
                current_row = []

    # Handle case where last row doesn't have tr_close (shouldn't happen, but defensive)
    if current_row:
        rows.append(current_row)

    # Pad uneven rows to match max row length
    if rows:
        max_length = max(len(row) for row in rows)
        for row in rows:
            while len(row) < max_length:
                row.append("")

    return rows


def _extract_text_from_inline_tokens(inline_tokens: list[Token]) -> str:
    """Extract plain text content from inline tokens.

    Recursively traverses inline token children to extract all text content,
    handling nested formatting tokens (bold, italic, etc.).

    Args:
        inline_tokens: List of inline tokens (typically one per cell).

    Returns:
        Plain text string with all formatting removed.
    """
    text_parts = []

    for token in inline_tokens:
        if token.children:
            # Recursively extract text from children
            for child in token.children:
                text_parts.append(_extract_text_from_token(child))
        else:
            # Leaf token - extract content
            text_parts.append(_extract_text_from_token(token))

    return "".join(text_parts).strip()


def _extract_text_from_token(token: Token) -> str:
    """Extract text content from a single token.

    Args:
        token: A markdown token.

    Returns:
        Text content string. Returns '\n' for softbreak and hardbreak tokens,
        and '\n' for html_inline tokens containing <br> tags.
    """
    if token.type == "text" or token.type == "code_inline":
        return token.content if hasattr(token, "content") else ""
    elif token.type == "softbreak" or token.type == "hardbreak":
        return "\n"
    elif token.type == "html_inline":
        # Check if content is a <br> tag (self-closing or with closing tag)
        content = token.content if hasattr(token, "content") else ""
        if content.strip().lower() in ("<br>", "<br/>", "<br />"):
            return "\n"
        return content
    elif token.children:
        # Recursively extract from children
        return "".join(_extract_text_from_token(child) for child in token.children)
    else:
        # Fallback: use content attribute if available
        return token.content if hasattr(token, "content") else ""


def _tokens_to_csv(rows: list[list[str]]) -> str:
    """Convert table rows to CSV format string.

    Uses Python's csv.writer to properly escape special characters (commas,
    quotes, newlines) according to CSV standards.

    Args:
        rows: List of rows, where each row is a list of cell strings.

    Returns:
        CSV string with proper escaping. Empty string if rows is empty.

    Raises:
        Exception: If CSV conversion fails (logged as error in caller).
    """
    if not rows:
        return ""

    # Use StringIO buffer for CSV writing
    buffer = io.StringIO()

    # Configure CSV writer
    writer = csv.writer(
        buffer,
        delimiter=",",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )

    # Write all rows (header + data rows)
    for row in rows:
        writer.writerow(row)

    # Get CSV string from buffer
    csv_content = buffer.getvalue()

    # Remove trailing newline if present (we'll add it back in the fenced block)
    csv_content = csv_content.rstrip("\n")

    return csv_content


def _chunk_csv(
    csv_content: str, header_row: list[str], chunk_size: int = 2000
) -> list[str]:
    """Split large CSV content into chunks while preserving headers.

    Uses a greedy algorithm to split CSV content into chunks of maximum
    `chunk_size` characters. Each chunk starts with the header row to ensure
    the CSV remains valid and readable. This prevents Notion API validation
    errors by keeping individual CSV blocks under the 2000-character limit
    for rich_text.text.content.

    Parses CSV using csv.reader to properly handle embedded newlines from
    cells containing <br> tags, and rebuilds chunks using csv.writer to
    preserve proper CSV formatting.

    Args:
        csv_content: Complete CSV string to chunk.
        header_row: List of header cell strings (first row of table).
        chunk_size: Maximum size for each chunk in characters. Defaults to 2000.

    Returns:
        List of CSV strings, each starting with the header row. Returns
        single-item list with original content if CSV fits in one chunk.

    Example:
        >>> header = ["Col1", "Col2"]
        >>> csv = "Col1,Col2\\nVal1,Val2\\nVal3,Val4"
        >>> chunks = _chunk_csv(csv, header, chunk_size=20)
        >>> len(chunks) >= 2
        True
    """
    # If CSV content fits in one chunk, return as-is
    if len(csv_content) <= chunk_size:
        return [csv_content]

    # Parse CSV content using csv.reader to properly handle embedded newlines
    csv_reader = csv.reader(io.StringIO(csv_content))
    rows = list(csv_reader)

    # Handle empty CSV
    if not rows:
        return [csv_content] if csv_content else [""]

    # Use header_row if provided, otherwise use first parsed row
    if header_row:
        header = header_row
        data_rows = rows if len(rows) == 0 or rows[0] != header_row else rows[1:]
    else:
        header = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []

    # Handle empty header gracefully
    if not header and not data_rows:
        return [csv_content]

    # If no data rows, return header-only chunk
    if not data_rows:
        return [csv_content]

    # Serialize header to get its size
    header_buffer = io.StringIO()
    header_writer = csv.writer(
        header_buffer,
        delimiter=",",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    header_writer.writerow(header)
    header_csv = header_buffer.getvalue().rstrip("\n")
    header_size = len(header_csv)

    # Ensure chunk_size is at least large enough for header + one row
    min_chunk_size = header_size + 10  # Header + minimal row + newline
    original_chunk_size = chunk_size
    if chunk_size < min_chunk_size:
        chunk_size = min_chunk_size
        logger.warning(
            f"chunk_size ({original_chunk_size}) too small for header, "
            f"using {min_chunk_size}"
        )

    chunks = []
    current_chunk_rows = [header]
    current_chunk_buffer = io.StringIO()
    current_chunk_writer = csv.writer(
        current_chunk_buffer,
        delimiter=",",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    current_chunk_writer.writerow(header)

    for data_row in data_rows:
        # Serialize the row to calculate its size
        row_buffer = io.StringIO()
        row_writer = csv.writer(
            row_buffer,
            delimiter=",",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        row_writer.writerow(data_row)
        row_csv = row_buffer.getvalue()
        row_size = len(row_csv)

        # If single row exceeds chunk_size, include it anyway (log warning)
        if row_size > chunk_size:
            logger.warning(
                f"CSV row exceeds chunk size ({row_size} > {chunk_size}), "
                f"including anyway"
            )
            # If current chunk has data, finalize it first
            if len(current_chunk_rows) > 1:
                chunks.append(current_chunk_buffer.getvalue().rstrip("\n"))
                current_chunk_buffer = io.StringIO()
                current_chunk_writer = csv.writer(
                    current_chunk_buffer,
                    delimiter=",",
                    quotechar='"',
                    quoting=csv.QUOTE_MINIMAL,
                    lineterminator="\n",
                )
                current_chunk_writer.writerow(header)
                current_chunk_rows = [header]
            # Add the large row as its own chunk
            large_chunk_buffer = io.StringIO()
            large_chunk_writer = csv.writer(
                large_chunk_buffer,
                delimiter=",",
                quotechar='"',
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            large_chunk_writer.writerow(header)
            large_chunk_writer.writerow(data_row)
            chunks.append(large_chunk_buffer.getvalue().rstrip("\n"))
            current_chunk_buffer = io.StringIO()
            current_chunk_writer = csv.writer(
                current_chunk_buffer,
                delimiter=",",
                quotechar='"',
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            current_chunk_writer.writerow(header)
            current_chunk_rows = [header]
            continue

        # Check if adding this row would exceed chunk_size
        # Rebuild current chunk to get accurate size
        test_buffer = io.StringIO()
        test_writer = csv.writer(
            test_buffer,
            delimiter=",",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        for r in current_chunk_rows:
            test_writer.writerow(r)
        test_writer.writerow(data_row)
        test_size = len(test_buffer.getvalue())

        if test_size > chunk_size:
            # Finalize current chunk
            chunks.append(current_chunk_buffer.getvalue().rstrip("\n"))
            # Start new chunk with header
            current_chunk_buffer = io.StringIO()
            current_chunk_writer = csv.writer(
                current_chunk_buffer,
                delimiter=",",
                quotechar='"',
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            current_chunk_writer.writerow(header)
            current_chunk_rows = [header]

        # Add row to current chunk
        current_chunk_rows.append(data_row)
        current_chunk_writer.writerow(data_row)

    # Add final chunk if it has content
    if len(current_chunk_rows) > 1 or current_chunk_rows:  # More than just header
        chunks.append(current_chunk_buffer.getvalue().rstrip("\n"))

    return chunks if chunks else [csv_content]
