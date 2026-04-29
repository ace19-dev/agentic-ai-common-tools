from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from core.base_mcp import MCPResult


class BaseRetrievalBackend(ABC):
    """Abstract interface that every retrieval backend must implement.

    Concrete implementations:
      TfidfSQLiteRetrievalBackend — default, TF-IDF cosine similarity + SQLite
      VectorRetrievalBackend      — ChromaDB embedding-based semantic search
      PostgresRetrievalBackend    — PostgreSQL tsvector full-text search

    RAG conventions:
      - Chunked documents are stored with metadata {"_source_id": "<original_doc_id>",
        "_chunk_index": N, "_total_chunks": M}.
      - delete_chunks() removes all chunks for a given source document.
      - search() accepts metadata_filter to scope results (e.g. by source or category).
    """

    @abstractmethod
    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult: ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5,
               metadata_filter: Optional[Dict[str, Any]] = None) -> MCPResult:
        """Search the index.

        Args:
            query:           Natural language search query.
            top_k:           Maximum number of results to return.
            metadata_filter: Optional equality filter on metadata fields.
                             e.g. {"category": "billing"} or {"_source_id": "doc-001"}.
                             Multiple keys are ANDed together.
        """
        ...

    @abstractmethod
    def delete(self, doc_id: str) -> MCPResult: ...

    @abstractmethod
    def delete_chunks(self, source_id: str) -> MCPResult:
        """Delete all chunks whose metadata._source_id equals source_id.

        Use before re-indexing a chunked document to prevent stale chunks.
        """
        ...

    @abstractmethod
    def health_check(self) -> MCPResult: ...
