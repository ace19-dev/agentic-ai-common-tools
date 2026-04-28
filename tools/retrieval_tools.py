"""
LangChain @tool wrappers for the Retrieval MCP (TF-IDF document search).

The metadata parameter is a JSON string rather than a dict to avoid JSON
Schema compatibility issues with some LLM providers when binding tools.
"""
import json
from langchain_core.tools import tool
from mcp.retrieval import get_retrieval_mcp

_mcp = get_retrieval_mcp()


@tool
def retrieval_search(query: str, top_k: int = 5) -> str:
    """Search the indexed document corpus using TF-IDF cosine similarity.

    Use this to look up relevant knowledge base entries, FAQ answers, or any
    previously indexed documents based on a natural language query.

    Args:
        query: Natural language search query.
        top_k: Maximum number of results to return (default: 5).

    Returns:
        JSON array of result objects, each with keys:
          - 'id' (str): document identifier
          - 'content' (str): full document text
          - 'score' (float): cosine similarity score 0.0–1.0
          - 'metadata' (dict): arbitrary metadata attached at index time
        Returns '[]' if no documents are indexed or no matches found.
    """
    return _mcp.search(query, top_k=top_k).to_tool_str()


@tool
def retrieval_index(doc_id: str, content: str, metadata: str = "{}") -> str:
    """Add or update a document in the retrieval index.

    Use this to make new content searchable. If a document with the same
    doc_id already exists it is replaced.

    Args:
        doc_id: Unique identifier (e.g. 'faq-001', 'https://example.com/page').
        content: Full text content to index. Longer content yields better results.
        metadata: JSON string of arbitrary key-value pairs stored alongside
                  the document (e.g. '{"source": "manual", "category": "billing"}').

    Returns:
        'indexed' on success, or 'ERROR: ...' on failure.
    """
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}
    return _mcp.index(doc_id, content, metadata=meta).to_tool_str()


@tool
def retrieval_delete(doc_id: str) -> str:
    """Remove a document from the retrieval index by its ID.

    Args:
        doc_id: The document identifier to remove.

    Returns:
        'deleted' on success, or 'ERROR: ...' if the document was not found.
    """
    return _mcp.delete(doc_id).to_tool_str()
