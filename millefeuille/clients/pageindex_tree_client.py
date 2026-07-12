"""PageIndex tree client implementation.

This module provides a high-level interface for interacting with the PageIndex
tree structure extraction API, handling tree extraction from processed OCR documents
and markdown-to-tree conversion. The client follows the same architectural patterns
as PageIndexClient for consistency and maintainability.
"""

import json
import logging

import requests

from ..domain.config import TreeStructureConfig
from ..domain.models import DocumentTree, TreeNode
from .exceptions import PageIndexTreeError
from .tree_client import TreeClient

logger = logging.getLogger(__name__)


class PageIndexTreeClient(TreeClient):
    """Client for interacting with PageIndex tree structure extraction API.

    This client encapsulates all interactions with the PageIndex tree extraction
    service, including extracting tree structures from processed OCR documents and
    converting markdown content into tree structures. It follows the same patterns
    as PageIndexClient for API communication, error handling, and response parsing.

    Example:
        >>> config = TreeStructureConfig(summary=True, text=False, description=True)
        >>> client = PageIndexTreeClient(
        ...     config, "https://api.pageindex.ai", "your-api-key"
        ... )
        >>> tree = client.get_tree_structure("doc-123")
        >>> tree = client.process_markdown_to_tree(markdown_content, "document.md")
    """

    # Default timeout for HTTP requests (in seconds)
    DEFAULT_TIMEOUT = 10

    def __init__(
        self, config: TreeStructureConfig, base_url: str, api_key: str
    ) -> None:
        """Initialize the PageIndex tree client.

        Args:
            config: Tree structure configuration containing extraction options
                (summary, text, description flags).
            base_url: Base URL for PageIndex API (e.g., "https://api.pageindex.ai").
            api_key: PageIndex API key for authentication.

        Raises:
            PageIndexTreeError: If client initialization fails (e.g., invalid API key).
        """
        try:
            self.config = config
            self.base_url = base_url
            self.api_key = api_key
            self._session = requests.Session()

            # Set default headers with Authorization
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {api_key}",
                }
            )

            logger.info(
                f"PageIndexTreeClient initialized successfully (base_url: {base_url})"
            )
        except Exception as e:
            error_msg = f"Failed to initialize PageIndex tree client: {str(e)}"
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Centralized API request handler.

        Constructs the full URL, makes the request, handles common errors,
        and returns the response object. Follows the same pattern as
        PageIndexClient._make_request().

        Args:
            method: HTTP method (GET, POST, etc.).
            endpoint: API endpoint path (e.g., "/doc/{doc_id}/").
            **kwargs: Additional arguments to pass to requests.Session.request().
                If json data is provided, Content-Type will be set automatically.

        Returns:
            Response object from the API request.

        Raises:
            PageIndexTreeError: If request fails or returns error status codes.
        """
        url = f"{self.base_url}{endpoint}"

        # Set Content-Type for JSON requests if json parameter is provided
        # and files is not provided (which would indicate multipart/form-data)
        if "json" in kwargs and "files" not in kwargs:
            kwargs.setdefault("headers", {}).update(
                {"Content-Type": "application/json"}
            )

        # Set default timeout if not explicitly provided
        kwargs.setdefault("timeout", self.DEFAULT_TIMEOUT)

        try:
            response = self._session.request(method, url, **kwargs)

            # Log request details
            logger.debug(f"API request: {method} {endpoint} -> {response.status_code}")

            # Handle common errors
            if response.status_code == 401 or response.status_code == 403:
                error_msg = (
                    f"Authentication failed: {response.status_code} {response.reason}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)
            elif response.status_code == 404:
                error_msg = (
                    f"Resource not found: {response.status_code} {response.reason}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)
            elif 400 <= response.status_code < 500:
                # Other 4xx client errors
                # Capture response body for 400 errors to aid diagnosis
                error_details = ""
                if response.status_code == 400:
                    try:
                        error_body = response.text
                        error_details = f" Response body: {error_body}"
                    except Exception as e:
                        error_details = f" Could not read response body: {str(e)}"
                error_msg = (
                    f"Client error: {response.status_code} {response.reason}"
                    f"{error_details}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)
            elif response.status_code >= 500:
                error_msg = f"Server error: {response.status_code} {response.reason}"
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            return response

        except requests.RequestException as e:
            error_msg = f"Request failed: {method} {endpoint} - {str(e)}"
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e
        except PageIndexTreeError:
            # Re-raise tree errors
            raise
        except Exception as e:
            error_msg = (
                f"Unexpected error in API request: {method} {endpoint} - {str(e)}"
            )
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def get_tree_structure(self, doc_id: str) -> DocumentTree:
        """Extract tree structure from an already-processed OCR document.

        This method retrieves the tree structure for a document that has already
        been processed by the PageIndex OCR service. The document must have been
        uploaded and processed before calling this method.

        The API returns responses in a nested structure with `status` and
        `result` fields. This method validates the response structure, checks the
        processing status, and extracts the tree data from the `result` field. The
        `result` field is required and must be a dictionary. No backward
        compatibility fallback is provided.

        Response structure:
        {
            "doc_id": "doc_123",
            "status": "completed",  # Must be "completed" for successful extraction
            "retrieval_ready": true,
            "result": {  # Required field containing tree data
                "doc_id": "doc_123",
                "doc_name": "test.pdf",
                "nodes": [...]
            }
        }

        This implementation follows the same pattern as PageIndexClient for handling
        nested API responses, but without backward compatibility fallbacks.

        Args:
            doc_id: Document identifier from OCR upload. This is the same identifier
                returned when uploading a document to PageIndex OCR.

        Returns:
            DocumentTree domain model containing the document's hierarchical tree
            structure.

        Raises:
            PageIndexTreeError: If tree extraction fails, API communication fails,
                response parsing fails, document is still processing
                (status != "completed"), result field is missing, result field is
                not a dictionary, or response is not a dictionary type. Error
                messages include document ID and status information for debugging.
        """
        try:
            logger.info(
                f"Extracting tree structure from OCR document (doc_id: {doc_id})"
            )

            # Build query parameters from config
            params = {"type": "tree"}
            if self.config.summary:
                params["summary"] = "true"
            if self.config.text:
                params["text"] = "true"
            if self.config.description:
                params["description"] = "true"

            # Make GET request to tree endpoint
            response = self._make_request("GET", f"/doc/{doc_id}/", params=params)

            # Parse JSON response
            try:
                response_data = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse tree response: {str(e)}"
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg, original_exception=e) from e

            # Validate response type and extract nested structure
            # The API returns nested responses with status and result fields,
            # following the same pattern as PageIndexClient but without backward
            # compatibility fallbacks
            if not isinstance(response_data, dict):
                error_msg = (
                    f"Invalid tree response type: {type(response_data)}. "
                    f"Expected dictionary."
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            # Check status field if present - documents must be completed before
            # tree extraction. Status values: "completed" (ready), "processing"
            # (in progress), "pending" (queued)
            status = response_data.get("status")
            if status and status != "completed":
                doc_id_from_response = response_data.get("doc_id", doc_id)
                error_msg = (
                    f"Document {doc_id_from_response} is still processing "
                    f"(status: {status})"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            # Extract result field (required, no fallback)
            # The result field contains the actual tree structure data and is mandatory
            if "result" not in response_data:
                available_keys = list(response_data.keys())
                error_msg = (
                    f"Missing required 'result' field in tree response for "
                    f"doc_id {doc_id}. Available keys: {available_keys}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            result_data = response_data["result"]

            # Handle case where result is a list (API may return list directly)
            if isinstance(result_data, list):
                # If result is a list, wrap it in a dict structure expected by
                # _parse_tree_response. This handles API responses where the
                # tree nodes are returned directly as a list
                logger.warning(
                    f"Tree response 'result' field is a list for doc_id {doc_id}, "
                    f"wrapping in dict structure"
                )
                # Store the list before reassigning result_data
                nodes_list = result_data
                result_data = {
                    "doc_id": doc_id,
                    "doc_name": response_data.get("doc_name", doc_id),
                    "nodes": nodes_list,
                }
            elif not isinstance(result_data, dict):
                error_msg = (
                    f"Tree response 'result' field must be a dictionary or list, "
                    f"got {type(result_data)} for doc_id {doc_id}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            # Parse response into DocumentTree using extracted result data
            # The _parse_tree_response method handles flexible field extraction
            # from the result data
            tree = self._parse_tree_response(result_data, doc_id)

            node_count = self._count_nodes(tree.nodes)
            logger.info(
                f"Successfully extracted tree structure "
                f"(doc_id: {doc_id}, nodes: {node_count})"
            )
            return tree

        except PageIndexTreeError:
            # Re-raise tree errors
            raise
        except Exception as e:
            error_msg = (
                f"Unexpected error extracting tree structure for doc_id {doc_id}: "
                f"{str(e)}"
            )
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def process_markdown_to_tree(self, markdown: str, filename: str) -> DocumentTree:
        """Convert markdown content into a tree structure.

        This method processes markdown content and extracts a hierarchical tree
        structure representing the document's organization (e.g., headings, sections,
        table of contents).

        Enhanced error logging is provided for 400 Bad Request errors to aid in
        diagnosis. When a 400 error occurs, the full response body, filename, and
        request parameters are logged before the error is re-raised. This helps
        determine if the endpoint exists, what request format is expected, or if
        authentication changes are needed.

        Args:
            markdown: Markdown content string to convert into a tree structure.
            filename: Filename for identification and logging purposes.

        Returns:
            DocumentTree domain model containing the extracted tree structure from
            the markdown content.

        Raises:
            PageIndexTreeError: If markdown processing fails, API communication fails,
                or response parsing fails. For 400 errors, enhanced logging includes
                full response body, filename, and request parameters for diagnosis.
        """
        try:
            logger.info(f"Converting markdown to tree structure (filename: {filename})")

            # Fix filename extension: API requires .md or .markdown extension
            # If filename has .pdf extension or no extension, change it to .md
            if filename.lower().endswith(".pdf"):
                # Replace .pdf with .md
                api_filename = filename[:-4] + ".md"
            elif not filename.lower().endswith((".md", ".markdown")):
                # Add .md extension if no markdown extension present
                api_filename = filename + ".md"
            else:
                # Already has correct extension
                api_filename = filename

            # Prepare multipart/form-data request with markdown file
            files = {"file": (api_filename, markdown.encode("utf-8"), "text/markdown")}

            # Build query parameters from config
            params = {}
            if self.config.summary:
                params["summary"] = "true"
            if self.config.text:
                params["text"] = "true"
            if self.config.description:
                params["description"] = "true"

            # Make POST request to markdown endpoint
            try:
                response = self._make_request(
                    "POST", "/markdown/", files=files, params=params
                )
            except PageIndexTreeError as e:
                # Enhanced error logging for 400 Bad Request errors from markdown
                # endpoint. Captures full response body, filename, and parameters
                # to enable diagnosis of endpoint existence, request format
                # requirements, or authentication issues
                if "Client error: 400" in str(e):
                    logger.error(
                        f"Markdown-to-tree endpoint returned 400 Bad Request. "
                        f"Filename: {filename}, Params: {params}. "
                        f"Full error: {str(e)}"
                    )
                # Re-raise the original error with enhanced context
                raise

            # Parse JSON response
            try:
                response_data = response.json()
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse markdown-to-tree response: {str(e)}"
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg, original_exception=e) from e

            # Validate response type and extract nested structure if present
            # The markdown endpoint may return either a flat structure (backward
            # compatibility) or a nested structure with status and result fields
            # (new contract)
            if not isinstance(response_data, dict):
                error_msg = (
                    f"Invalid markdown-to-tree response type: "
                    f"{type(response_data)}. Expected dictionary."
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            # Check if response has nested structure (status and result fields)
            if "status" in response_data and "result" in response_data:
                # Nested structure: validate status and extract result
                status = response_data.get("status")
                if status and status != "completed":
                    error_msg = (
                        f"Markdown processing not completed (status: {status}) "
                        f"for {filename}"
                    )
                    logger.error(error_msg)
                    raise PageIndexTreeError(error_msg)

                # Extract result field (required for nested structure)
                result_data = response_data["result"]
                if not isinstance(result_data, dict):
                    error_msg = (
                        f"Markdown-to-tree response 'result' field must be a "
                        f"dictionary, got {type(result_data)} for {filename}"
                    )
                    logger.error(error_msg)
                    raise PageIndexTreeError(error_msg)

                # Parse response into DocumentTree using extracted result data
                tree = self._parse_tree_response(result_data, filename)
            else:
                # Flat structure (backward compatibility): parse directly
                tree = self._parse_tree_response(response_data, filename)

            node_count = self._count_nodes(tree.nodes)
            logger.info(
                f"Successfully converted markdown to tree structure "
                f"(filename: {filename}, nodes: {node_count})"
            )
            return tree

        except PageIndexTreeError:
            # Re-raise tree errors
            raise
        except Exception as e:
            error_msg = (
                f"Unexpected error converting markdown to tree for {filename}: {str(e)}"
            )
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def _parse_tree_response(
        self, response_data: dict, doc_identifier: str
    ) -> DocumentTree:
        """Parse API response into DocumentTree domain model.

        Extracts document metadata and tree nodes from the API response,
        handling various field name variations and optional fields.

        Args:
            response_data: JSON response data from PageIndex tree API.
            doc_identifier: Fallback identifier if doc_id is not found in response.

        Returns:
            DocumentTree domain model with parsed metadata and nodes.

        Raises:
            PageIndexTreeError: If parsing fails or required fields are missing.
        """
        try:
            # Extract doc_id from response (try multiple field names)
            doc_id = None
            for field_name in ["doc_id", "id", "document_id"]:
                if field_name in response_data:
                    doc_id = str(response_data[field_name])
                    break

            # Fallback to doc_identifier if not found
            if not doc_id:
                doc_id = doc_identifier

            # Extract doc_name from response (try multiple field names)
            doc_name = None
            for field_name in ["doc_name", "name", "filename"]:
                if field_name in response_data:
                    doc_name = str(response_data[field_name])
                    break

            # Fallback to doc_identifier if not found
            if not doc_name:
                doc_name = doc_identifier

            # Extract optional description if present and config enabled
            description = None
            if self.config.description and "description" in response_data:
                description = (
                    str(response_data["description"])
                    if response_data["description"]
                    else None
                )

            # Extract nodes array from response (try multiple field names)
            nodes_data = None
            for field_name in ["nodes", "tree", "structure"]:
                if field_name in response_data:
                    nodes_data = response_data[field_name]
                    break

            if nodes_data is None:
                error_msg = (
                    f"Could not find nodes array in tree response: "
                    f"{list(response_data.keys())}"
                )
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            if not isinstance(nodes_data, list):
                error_msg = f"Nodes data is not a list: {type(nodes_data)}"
                logger.error(error_msg)
                raise PageIndexTreeError(error_msg)

            # Parse nodes recursively
            parsed_nodes = self._parse_tree_nodes(nodes_data)

            # Construct and return DocumentTree
            return DocumentTree(
                doc_id=doc_id,
                doc_name=doc_name,
                description=description,
                nodes=parsed_nodes,
            )

        except PageIndexTreeError:
            # Re-raise tree errors
            raise
        except Exception as e:
            error_msg = f"Failed to parse tree response: {str(e)}"
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def _parse_tree_nodes(self, nodes_data: list[dict]) -> list[TreeNode]:
        """Recursively parse tree nodes from API response.

        Converts a list of node dictionaries into `TreeNode` objects, handling
        nested child nodes recursively. Validates required fields and extracts
        optional fields based on configuration.

        This method handles field name variations (e.g., "node_id" vs "id",
        "page_index" vs "page" vs "page_number") and normalizes page indices
        from 1-indexed to 0-indexed format as required by the `TreeNode` domain model.

        Args:
            nodes_data: List of node dictionaries from API response. Each dictionary
                may contain nested "nodes" arrays for child nodes, which are parsed
                recursively.

        Returns:
            List of `TreeNode` objects with parsed fields and nested children.
            All nodes are fully parsed including all levels of nesting.

        Raises:
            PageIndexTreeError: If parsing fails or required fields are missing.
        """
        try:
            parsed_nodes = []

            for node_data in nodes_data:
                try:
                    # Extract required fields (try multiple field names)
                    node_id = None
                    for field_name in ["node_id", "id"]:
                        if field_name in node_data:
                            node_id = str(node_data[field_name])
                            break

                    title = None
                    for field_name in ["title", "heading", "name"]:
                        if field_name in node_data:
                            title = str(node_data[field_name])
                            break

                    page_index = None
                    for field_name in ["page_index", "page", "page_number"]:
                        if field_name in node_data:
                            page_index_value = node_data[field_name]
                            # Normalize to 0-indexed: page and page_number may be
                            # 1-indexed, but TreeNode.page_index must be 0-indexed
                            # per domain model contract
                            if isinstance(page_index_value, int):
                                # Convert from 1-indexed to 0-indexed for
                                # page/page_number fields. The "page_index" field
                                # is assumed to already be 0-indexed
                                if field_name in ["page", "page_number"]:
                                    page_index = page_index_value - 1
                                else:  # page_index field (already 0-indexed)
                                    page_index = page_index_value
                            elif isinstance(page_index_value, str):
                                try:
                                    # Convert string values: page/page_number from
                                    # 1-indexed to 0-indexed. The "page_index"
                                    # field is assumed to already be 0-indexed
                                    if field_name in ["page", "page_number"]:
                                        page_index = int(page_index_value) - 1
                                    else:  # page_index field (already 0-indexed)
                                        page_index = int(page_index_value)
                                except ValueError:
                                    pass
                            break

                    # Validate required fields
                    if not node_id:
                        logger.warning(
                            f"Skipping node with missing node_id: {node_data}"
                        )
                        continue

                    if not title:
                        logger.warning(f"Skipping node with missing title: {node_data}")
                        continue

                    if page_index is None:
                        logger.warning(
                            f"Skipping node with missing page_index: {node_data}"
                        )
                        continue

                    # Extract optional text field if present and config enabled
                    text = None
                    if self.config.text and "text" in node_data:
                        text_value = node_data["text"]
                        text = str(text_value) if text_value else None

                    # Extract optional summary field if present and config enabled
                    summary = None
                    if self.config.summary and "summary" in node_data:
                        summary_value = node_data["summary"]
                        summary = str(summary_value) if summary_value else None

                    # Extract optional child nodes and parse recursively
                    child_nodes = []
                    if "nodes" in node_data and isinstance(node_data["nodes"], list):
                        child_nodes = self._parse_tree_nodes(node_data["nodes"])

                    # Construct TreeNode object
                    node = TreeNode(
                        node_id=node_id,
                        title=title,
                        page_index=page_index,
                        text=text,
                        summary=summary,
                        nodes=child_nodes,
                    )

                    parsed_nodes.append(node)

                except Exception as e:
                    logger.warning(f"Failed to parse node: {str(e)}, skipping")
                    # Continue processing remaining nodes
                    continue

            return parsed_nodes

        except PageIndexTreeError:
            # Re-raise tree errors
            raise
        except Exception as e:
            error_msg = f"Failed to parse tree nodes: {str(e)}"
            logger.error(error_msg)
            raise PageIndexTreeError(error_msg, original_exception=e) from e

    def _count_nodes(self, nodes: list[TreeNode]) -> int:
        """Recursively count total nodes in tree structure.

        Helper method for logging node counts. Recursively counts all nodes
        including nested children. This method traverses the entire tree structure
        depth-first, counting each node and all of its descendants.

        Args:
            nodes: List of `TreeNode` objects to count. Each node may contain nested
                child nodes in its `nodes` attribute, which are counted recursively.

        Returns:
            Total count of all nodes including nested children. Returns 0 if the
            nodes list is empty.
        """
        count = len(nodes)
        for node in nodes:
            if node.nodes:
                count += self._count_nodes(node.nodes)
        return count
