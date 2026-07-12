"""
Tree structure processor for extracting and persisting document tree structures.

This module provides the TreeStructureProcessor class, which acts as a high-level
orchestrator for tree structure operations. The processor uses dependency injection
to receive a TreeClient instance, enabling provider-agnostic tree extraction. It
provides three core methods: extracting trees from OCR results, processing markdown
to trees, and saving trees to disk as JSON.

The processor follows the same patterns as existing domain services, using
comprehensive logging, graceful error handling, and returning domain models. The
save_to_disk method integrates with the existing pipeline disk persistence pattern,
creating JSON files in the format {item_dir}/{pdf_name}_tree_structure.json.

Example usage:
    >>> from millefeuille.clients.pageindex_tree_client import (
    ...     PageIndexTreeClient
    ... )
    >>> from millefeuille.domain.config import TreeStructureConfig
    >>> from millefeuille.domain.tree_processor import TreeStructureProcessor
    >>>
    >>> # Initialize processor with tree client
    >>> config = TreeStructureConfig(summary=True, text=False, description=True)
    >>> client = PageIndexTreeClient(config, "https://api.pageindex.ai", "api-key")
    >>> processor = TreeStructureProcessor(client)
    >>>
    >>> # Extract tree from processed OCR document
    >>> tree = processor.extract_from_ocr_result("doc-123")
    >>>
    >>> # Process markdown to tree structure
    >>> tree = processor.process_from_markdown(markdown_content, "document.md")
    >>>
    >>> # Save tree to disk
    >>> from pathlib import Path
    >>> processor.save_to_disk(tree, Path("/output/item_dir"), "document.pdf")
"""

import json
import logging
from pathlib import Path

from ..clients.exceptions import OCRClientError, TreeStructureProcessingError
from ..clients.tree_client import TreeClient
from .models import DocumentTree, TreeNode

logger = logging.getLogger(__name__)


class TreeStructureProcessor:
    """High-level orchestrator for tree structure extraction and persistence.

    This processor provides a unified interface for tree structure operations,
    abstracting away provider-specific details through dependency injection of
    a TreeClient implementation. The processor handles tree extraction from
    OCR results, markdown-to-tree conversion, and disk persistence as JSON files.

    The processor follows the same architectural patterns as other domain services,
    using comprehensive logging, graceful error handling, and returning domain models.
    Tree extraction errors are re-raised to allow callers to handle them, while disk
    save errors are logged but not raised (disk persistence is optional).

    Attributes:
        tree_client: TreeClient instance used for tree extraction operations.
            Any TreeClient implementation can be injected (PageIndex, future providers).
        logger: Logger instance for this processor, scoped to the module name.

    Example:
        >>> from millefeuille.clients.pageindex_tree_client import (
    ...     PageIndexTreeClient
    ... )
        >>> from millefeuille.domain.config import (
        ...     TreeStructureConfig
        ... )
        >>> from millefeuille.domain.tree_processor import (
        ...     TreeStructureProcessor
        ... )
        >>>
        >>> config = TreeStructureConfig(summary=True, text=False, description=True)
        >>> client = PageIndexTreeClient(config, "https://api.pageindex.ai", "api-key")
        >>> processor = TreeStructureProcessor(client)
        >>>
        >>> # Extract tree from processed OCR document
        >>> tree = processor.extract_from_ocr_result("doc-123")
        >>>
        >>> # Save tree to disk
        >>> from pathlib import Path
        >>> processor.save_to_disk(tree, Path("/output/item_dir"), "document.pdf")
    """

    def __init__(self, tree_client: TreeClient) -> None:
        """Initialize the tree structure processor with dependency injection.

            The processor accepts any TreeClient implementation,
            enabling provider-agnostic
            tree extraction. This follows the dependency injection pattern
            used throughout
            the codebase, making the processor easy to test and allowing for different
            provider implementations (PageIndex, future providers).

            Args:
                tree_client: TreeClient interface instance. Any
                implementation can be used
                    (PageIndexTreeClient, future providers). The client handles all
                    provider-specific details internally.

            Example:
                >>> from millefeuille.clients.pageindex_tree_client import (
        ...     PageIndexTreeClient
        ... )
                >>> from millefeuille.domain.config import (
                ...     TreeStructureConfig
                ... )
                >>> from millefeuille.domain.tree_processor import (
                ...     TreeStructureProcessor
                ... )
                >>>
                >>> config = TreeStructureConfig(
                ...     summary=True, text=False, description=True
                ... )
                >>> client = PageIndexTreeClient(
                ...     config, "https://api.pageindex.ai", "api-key"
                ... )
                >>> processor = TreeStructureProcessor(client)
        """
        self.tree_client = tree_client
        self.logger = logging.getLogger(__name__)

    def extract_from_ocr_result(self, doc_id: str) -> DocumentTree:
        """Extract tree structure from an already-processed OCR document.

        This method extracts the tree structure from a document that has already
        been processed by the OCR provider. The document must have been uploaded
        and processed before calling this method. The tree structure represents
        the document's hierarchical organization (e.g., table of contents, sections).

        This method delegates to `self.tree_client.get_tree_structure()` to perform
        the actual extraction, making it provider-agnostic through dependency injection.

        Args:
            doc_id: Document identifier from OCR upload. This is the same identifier
                returned when uploading a document to the OCR provider (e.g., from
                `OCRClient.upload_pdf()`).

        Returns:
            `DocumentTree` domain model containing the document's hierarchical
            tree structure, including root nodes and all nested child nodes.

        Raises:
            TreeStructureProcessingError: If tree extraction fails. The error
                includes context about the doc_id and operation that failed.
                Original client exceptions are preserved and re-raised without
                forcing them into provider-specific types.

        Example:
            >>> processor = TreeStructureProcessor(client)
            >>> tree = processor.extract_from_ocr_result("doc-123")
            >>> print(f"Extracted {len(tree.nodes)} root nodes")
        """
        try:
            self.logger.info(
                f"Extracting tree structure from OCR document (doc_id: {doc_id})"
            )

            tree = self.tree_client.get_tree_structure(doc_id)

            node_count = self._count_nodes(tree.nodes)
            self.logger.info(
                f"Successfully extracted tree structure "
                f"(doc_id: {doc_id}, nodes: {node_count})"
            )

            return tree

        except OCRClientError as e:
            # Re-raise OCR client errors (including provider-specific
            # subclasses) without wrapping
            error_msg = (
                f"Failed to extract tree structure for doc_id {doc_id}: {str(e)}"
            )
            self.logger.error(error_msg)
            raise TreeStructureProcessingError(error_msg, original_exception=e) from e
        except Exception as e:
            error_msg = (
                f"Unexpected error extracting tree structure for doc_id "
                f"{doc_id}: {str(e)}"
            )
            self.logger.error(error_msg)
            raise TreeStructureProcessingError(error_msg, original_exception=e) from e

    def process_from_markdown(self, markdown: str, filename: str) -> DocumentTree:
        """Convert markdown content into a tree structure.

        This method processes markdown content and extracts a hierarchical tree
        structure representing the document's organization (e.g., headings, sections,
        table of contents). The tree structure enables navigation and structured
        representation of document organization.

        This method delegates to `self.tree_client.process_markdown_to_tree()`
        to perform the actual conversion, making it provider-agnostic through
        dependency injection.

        Args:
            markdown: Markdown content string to convert into a tree structure.
            filename: Filename for identification and logging purposes. Used to
                identify the source document in logs and error messages.

        Returns:
            `DocumentTree` domain model containing the extracted tree structure from
            the markdown content, including root nodes and all nested child nodes.

        Raises:
            TreeStructureProcessingError: If markdown processing fails. The
                error includes context about the filename and operation that
                failed. Original client exceptions are preserved and re-raised
                without forcing them into provider-specific types.

        Example:
            >>> processor = TreeStructureProcessor(client)
            >>> markdown_content = "# Title\\n## Section 1\\nContent here"
            >>> tree = processor.process_from_markdown(markdown_content, "document.md")
            >>> print(f"Processed {len(tree.nodes)} root nodes")
        """
        try:
            self.logger.info(
                f"Converting markdown to tree structure (filename: {filename})"
            )

            tree = self.tree_client.process_markdown_to_tree(markdown, filename)

            node_count = self._count_nodes(tree.nodes)
            self.logger.info(
                f"Successfully converted markdown to tree structure "
                f"(filename: {filename}, nodes: {node_count})"
            )

            return tree

        except OCRClientError as e:
            # Re-raise OCR client errors (including provider-specific
            # subclasses) without wrapping
            error_msg = f"Failed to process markdown to tree for {filename}: {str(e)}"
            self.logger.error(error_msg)
            raise TreeStructureProcessingError(error_msg, original_exception=e) from e
        except Exception as e:
            error_msg = (
                f"Unexpected error processing markdown to tree for {filename}: {str(e)}"
            )
            self.logger.error(error_msg)
            raise TreeStructureProcessingError(error_msg, original_exception=e) from e

    def save_to_disk(self, tree: DocumentTree, item_dir: Path, pdf_name: str) -> None:
        """Save tree structure as JSON file to disk.

        This method saves the tree structure as a JSON file in the item's output
        directory. The filename follows the pattern
        {sanitized_pdf_name}_tree_structure.json.
        The pdf_name is sanitized by replacing '/' and '\\' with '_' and removing
        the '.pdf' extension if present.

        The method integrates with the existing pipeline disk persistence pattern,
        creating JSON files alongside other output files (markdown, summaries) in the
        item-specific directory. The tree is serialized using `DocumentTree.to_dict()`
        method and saved with UTF-8 encoding and JSON indentation for readability.
        Disk save errors are logged but not raised, as tree structure persistence is
        an optional operation that shouldn't fail the entire pipeline.

        Args:
            tree: `DocumentTree` domain model to save to disk. The tree is serialized
                using the `to_dict()` method before writing to JSON.
            item_dir: Path object pointing to the item's output directory.
                This directory
                is already created by the pipeline before calling this method.
            pdf_name: PDF filename string. Will be sanitized before creating the output
                filename. The '.pdf' extension will be removed if present.

        Example:
            >>> from pathlib import Path
            >>> processor = TreeStructureProcessor(client)
            >>> tree = processor.extract_from_ocr_result("doc-123")
            >>> processor.save_to_disk(tree, Path("/output/item_dir"), "document.pdf")
            >>> # Creates file: /output/item_dir/document_tree_structure.json
        """
        try:
            # Sanitize pdf_name: replace '/' and '\' with '_', remove '.pdf'
            # extension if present
            sanitized_pdf_name = pdf_name.replace("/", "_").replace("\\", "_")
            if sanitized_pdf_name.lower().endswith(".pdf"):
                sanitized_pdf_name = sanitized_pdf_name[:-4]

            # Create filename: {sanitized_pdf_name}_tree_structure.json
            filename = f"{sanitized_pdf_name}_tree_structure.json"
            file_path = item_dir / filename

            # Convert tree to dict using to_dict() method
            tree_dict = tree.to_dict()

            # Write JSON file with UTF-8 encoding and indentation
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(tree_dict, f, indent=2, ensure_ascii=False)

            self.logger.info(f"Successfully saved tree structure to {file_path}")

        except OSError as e:
            error_msg = (
                f"Failed to save tree structure to disk "
                f"(item_dir: {item_dir}, pdf_name: {pdf_name}): {str(e)}"
            )
            self.logger.warning(error_msg)
            # Don't raise - disk save is optional, shouldn't fail pipeline
        except Exception as e:
            error_msg = (
                f"Unexpected error saving tree structure to disk "
                f"(item_dir: {item_dir}, pdf_name: {pdf_name}): {str(e)}"
            )
            self.logger.warning(error_msg)
            # Don't raise - disk save is optional, shouldn't fail pipeline

    def _count_nodes(self, nodes: list[TreeNode]) -> int:
        """Recursively count total nodes in tree structure.

        Helper method for logging node counts after tree extraction. Recursively
        counts all nodes including nested children, providing accurate statistics
        for logging and debugging purposes.

        Args:
            nodes: List of TreeNode objects to count. Each node may contain nested
                child nodes, which are counted recursively.

        Returns:
            Total count of all nodes including nested children. Returns 0 if nodes
            list is empty.

        Example:
            >>> processor = TreeStructureProcessor(client)
            >>> tree = processor.extract_from_ocr_result("doc-123")
            >>> total_nodes = processor._count_nodes(tree.nodes)
            >>> print(f"Tree contains {total_nodes} total nodes")
        """
        count = len(nodes)
        for node in nodes:
            if node.nodes:
                count += self._count_nodes(node.nodes)
        return count
