"""
Abstract base class for tree client implementations.

This module defines the abstract interface that all tree structure extraction provider
implementations must follow. The interface supports two core workflows: extracting
tree structures from already-processed OCR documents and converting markdown content
into tree structures.

Example workflow:
    # 1. Extract tree from processed OCR document
    # tree = client.get_tree_structure(doc_id)

    # 2. Convert markdown to tree structure
    # tree = client.process_markdown_to_tree(markdown_content, "document.md")

Implementations must handle provider-specific details internally (e.g., API endpoints,
authentication, response parsing) and expose a consistent interface through this
abstract base class.
"""

from abc import ABC, abstractmethod

from ..domain.models import DocumentTree


class TreeClient(ABC):
    """Abstract base class for all tree structure extraction provider implementations.

    This class defines the interface that all tree clients must implement,
    ensuring consistent behavior across different tree extraction providers
    (PageIndex, etc.). The interface supports two core operations: extracting
    tree structures from already-processed OCR documents and converting
    markdown content into tree structures.

    Implementations should handle provider-specific details (API endpoints,
    authentication, response formats) internally and use document IDs or
    filenames as the primary interface for operations.
    """

    @abstractmethod
    def get_tree_structure(self, doc_id: str) -> DocumentTree:
        """Retrieve tree structure from an already-processed OCR document.

        This method extracts the tree structure from a document that has already
        been processed by the OCR provider. The document must have been uploaded
        and processed before calling this method.

        Args:
            doc_id: Document identifier from OCR upload. This is the same
                identifier returned when uploading a document to the OCR provider.

        Returns:
            DocumentTree domain model containing the document's hierarchical
            tree structure, including root nodes and all nested child nodes.

        Raises:
            OCRClientError: If tree extraction fails (provider-specific subclasses).
        """
        pass

    @abstractmethod
    def process_markdown_to_tree(self, markdown: str, filename: str) -> DocumentTree:
        """Convert markdown content into a tree structure.

        This method processes markdown content and extracts a hierarchical tree
        structure representing the document's organization (e.g., headings, sections,
        table of contents).

        Args:
            markdown: Markdown content string to convert into a tree structure.
            filename: Filename for identification and logging purposes. Used to
                identify the source document in logs and error messages.

        Returns:
            DocumentTree domain model containing the extracted tree structure from
            the markdown content, including root nodes and all nested child nodes.

        Raises:
            OCRClientError: If markdown processing fails (provider-specific subclasses).
        """
        pass
