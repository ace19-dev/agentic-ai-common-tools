"""
LangChain @tool wrappers for the Retrieval MCP.

RAG flow:
  1. crawl_and_index / retrieval_index  ← ingest + chunk documents
  2. retrieval_build_context            ← retrieve + assemble LLM-ready context
  3. (Optionally) retrieval_search      ← raw search results with scores

The metadata parameter is a JSON string rather than a dict to avoid JSON
Schema compatibility issues with some LLM providers when binding tools.
"""
import json
from typing import Optional

from langchain_core.tools import tool

from mcp.retrieval import get_retrieval_mcp

_mcp = get_retrieval_mcp()


# ── Indexing ──────────────────────────────────────────────────────────────────

@tool
def retrieval_index(doc_id: str, content: str, metadata: str = "{}",
                    chunk_size: int = 0, chunk_overlap: int = 50) -> str:
    """Add or update a document in the retrieval index, with optional chunking.

    For RAG use cases, set chunk_size > 0 to split long documents into
    overlapping chunks. Each chunk is stored as "{doc_id}__chunk_{N}" with
    metadata._source_id pointing back to the original doc_id.

    Before re-indexing a chunked document, call retrieval_delete_chunks(doc_id)
    to remove stale chunks from a previous version.

    Args:
        doc_id:       Unique identifier (e.g. 'faq-001', 'https://example.com/page').
        content:      Full text content to index.
        metadata:     JSON string of key-value pairs (e.g. '{"category": "billing"}').
        chunk_size:   Characters per chunk. 0 = index as single document (default).
        chunk_overlap: Character overlap between consecutive chunks (default: 50).

    Returns:
        'indexed' or 'indexed: N chunks from <doc_id>' on success, 'ERROR: ...' on failure.
    """
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}

    if chunk_size <= 0:
        return _mcp.index(doc_id, content, metadata=meta).to_tool_str()

    from mcp.backends.retrieval.chunker import TextChunker
    chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = chunker.split(content)
    if not chunks:
        return "ERROR: no content to index after chunking"

    errors = []
    for i, chunk in enumerate(chunks):
        chunk_meta = {
            **meta,
            "_source_id": doc_id,
            "_chunk_index": i,
            "_total_chunks": len(chunks),
        }
        result = _mcp.index(f"{doc_id}__chunk_{i}", chunk, metadata=chunk_meta)
        if not result.success:
            errors.append(f"chunk {i}: {result.error}")

    if errors:
        return f"ERROR: {len(errors)}/{len(chunks)} chunks failed — {errors[0]}"
    return f"indexed: {len(chunks)} chunks from '{doc_id}'"


@tool
def retrieval_delete_chunks(source_doc_id: str) -> str:
    """Delete all indexed chunks belonging to a source document.

    Use this before re-indexing a chunked document to prevent stale chunks
    from appearing in search results.

    Args:
        source_doc_id: The original doc_id used when indexing with chunk_size > 0.

    Returns:
        'deleted: N chunks' on success, or 'ERROR: ...' on failure.
    """
    return _mcp.delete_chunks(source_doc_id).to_tool_str()


@tool
def retrieval_delete(doc_id: str) -> str:
    """Remove a single document (or chunk) from the retrieval index by its ID.

    To remove all chunks of a source document, use retrieval_delete_chunks instead.

    Args:
        doc_id: The document identifier to remove.

    Returns:
        'deleted' on success, or 'ERROR: ...' if the document was not found.
    """
    return _mcp.delete(doc_id).to_tool_str()


# ── Search & RAG context ──────────────────────────────────────────────────────

@tool
def retrieval_search(query: str, top_k: int = 5, filter: str = "{}") -> str:
    """Search the indexed document corpus and return raw results with scores.

    For injecting retrieved knowledge into an LLM prompt, prefer
    retrieval_build_context which formats results as a ready-to-use context string.

    Args:
        query:  Natural language search query.
        top_k:  Maximum number of results to return (default: 5).
        filter: JSON metadata filter for scoping results.
                e.g. '{"category": "billing"}' or '{"_source_id": "faq-001"}'.
                Multiple keys are ANDed together.

    Returns:
        JSON array of result objects with keys: id, content, score, metadata.
        Returns '[]' if no documents are indexed or no matches found.
    """
    metadata_filter = _parse_filter(filter)
    return _mcp.search(query, top_k=top_k, metadata_filter=metadata_filter).to_tool_str()


@tool
def retrieval_build_context(query: str, top_k: int = 5,
                             max_chars: int = 3000,
                             filter: str = "{}") -> str:
    """Retrieve relevant chunks and assemble them into an LLM-ready context string.

    This is the primary RAG tool. Use it to inject retrieved knowledge into an
    LLM prompt. Results are formatted with source attribution and score, and
    automatically truncated to stay within max_chars.

    Args:
        query:     Natural language question or search query.
        top_k:     Maximum number of chunks to retrieve (default: 5).
        max_chars: Maximum total character length of the assembled context
                   (roughly 750 tokens at 4 chars/token). Default: 3000.
        filter:    JSON metadata filter, e.g. '{"category": "billing"}'.

    Returns:
        Formatted context string:

          [Source: doc-id | Score: 0.85]
          First relevant chunk content...

          ---

          [Source: doc-id | Score: 0.72]
          Second relevant chunk content...

        Returns 'No relevant context found.' when nothing matches.
    """
    metadata_filter = _parse_filter(filter)
    result = _mcp.search(query, top_k=top_k, metadata_filter=metadata_filter)
    if not result.success:
        return result.to_tool_str()

    hits = result.data or []
    if not hits:
        return "No relevant context found."

    parts = []
    total = 0
    for hit in hits:
        source = (hit.get("metadata") or {}).get("_source_id") or hit.get("id", "unknown")
        score = hit.get("score", 0.0)
        content = hit.get("content", "")
        header = f"[Source: {source} | Score: {score:.2f}]"
        block = f"{header}\n{content}"

        if total + len(block) > max_chars:
            remaining = max_chars - total - len(header) - 1
            if remaining > 80:
                parts.append(f"{header}\n{content[:remaining]}")
            break

        parts.append(block)
        total += len(block)

    return "\n\n---\n\n".join(parts) if parts else "No relevant context found."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_filter(filter_str: str) -> Optional[dict]:
    if not filter_str or filter_str.strip() in ("{}", ""):
        return None
    try:
        parsed = json.loads(filter_str)
        return parsed if isinstance(parsed, dict) and parsed else None
    except json.JSONDecodeError:
        return None
