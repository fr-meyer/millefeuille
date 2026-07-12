"""Temporary file utilities for PDF processing.

This module provides context manager utilities for creating and managing
temporary PDF files during processing operations. All utilities ensure
proper cleanup even when exceptions occur.
"""

from collections.abc import Generator
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)


@contextmanager
def temporary_pdf_file(pdf_bytes: bytes, filename: str) -> Generator[Path, None, None]:
    """Context manager for creating and automatically cleaning up temporary PDF files.

    This function creates a temporary PDF file from the provided bytes, yields the path
    to the caller for processing, and ensures the file is cleaned up when the context
    exits, even if an exception occurs. Cleanup errors are logged as warnings but do not
    raise exceptions to avoid masking original errors.

    The file handle is closed before yielding to allow external processes to access the
    file without file locking issues.

    Args:
        pdf_bytes: PDF file content as bytes.
        filename: Original filename for logging/debugging purposes (not used for temp
            file naming).

    Yields:
        Path: Path object pointing to the temporary PDF file.

    Example:
        >>> with temporary_pdf_file(pdf_bytes, "document.pdf") as temp_path:
        ...     # Use temp_path for processing
        ...     result = process_pdf(temp_path)
        ...     # File is automatically cleaned up after this block

    Notes:
        - The temporary file is created with `.pdf` suffix.
        - Cleanup failures are logged as warnings, not errors.
        - The file handle is closed before yielding to allow external processes to
          access it.
    """
    temp_path = None
    temp_path_str = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path_str = temp_file.name
            temp_file.write(pdf_bytes)
            temp_file.flush()
        temp_path = Path(temp_path_str)
        logger.debug(f"Created temporary PDF file for '{filename}': {temp_path}")
    except Exception:
        # Attempt to unlink partially created file
        if temp_path_str is not None:
            try:
                os.unlink(temp_path_str)
            except Exception as unlink_error:
                logger.warning(
                    f"Failed to cleanup partially created temporary file "
                    f"{temp_path_str}: {str(unlink_error)}"
                )

        # Re-raise the original exception
        raise

    try:
        yield temp_path
    finally:
        if temp_path.exists():
            try:
                os.unlink(temp_path)
                logger.debug(
                    f"Cleaned up temporary PDF file for '{filename}': {temp_path}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup temporary PDF file {temp_path}: {str(e)}"
                )
        else:
            logger.debug(f"Temporary PDF file already deleted: {temp_path}")
