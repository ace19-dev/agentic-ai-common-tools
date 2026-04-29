"""
Retrieval MCP — document search with pluggable backends.

Backend is selected via the RETRIEVAL_BACKEND environment variable:
  tfidf_sqlite (default) — TF-IDF cosine similarity + SQLite document store
  vector                 — ChromaDB embedding-based semantic search
  postgres               — PostgreSQL tsvector full-text search
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import config
from core.base_mcp import BaseMCP, MCPResult
from mcp.backends.retrieval.base import BaseRetrievalBackend

logger = logging.getLogger(__name__)


class RetrievalMCP(BaseMCP):
    """Document retrieval store. Delegates all operations to a backend.

    The concrete backend is chosen at construction time via RETRIEVAL_BACKEND env var.
    """

    def __init__(self, backend: BaseRetrievalBackend):
        self._backend = backend

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        return self._backend.index(doc_id, content, metadata=metadata)

    def search(self, query: str, top_k: int = 5,
               metadata_filter: Optional[Dict[str, Any]] = None) -> MCPResult:
        return self._backend.search(query, top_k=top_k, metadata_filter=metadata_filter)

    def delete(self, doc_id: str) -> MCPResult:
        return self._backend.delete(doc_id)

    def delete_chunks(self, source_id: str) -> MCPResult:
        """Delete all chunks whose metadata._source_id equals source_id."""
        return self._backend.delete_chunks(source_id)

    def health_check(self) -> MCPResult:
        result = self._backend.health_check()
        if result.success and isinstance(result.data, dict):
            result.data["mcp"] = "retrieval"
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[RetrievalMCP] = None


def _create_backend() -> BaseRetrievalBackend:
    backend_type = config.RETRIEVAL_BACKEND.lower()
    if backend_type == "vector":
        from mcp.backends.retrieval.vector import VectorRetrievalBackend
        logger.info("Retrieval backend: ChromaDB (vector)")
        return VectorRetrievalBackend(
            path=config.RETRIEVAL_VECTOR_PATH,
            collection_name=config.RETRIEVAL_VECTOR_COLLECTION,
        )
    if backend_type == "postgres":
        from mcp.backends.retrieval.postgres import PostgresRetrievalBackend
        if not config.RETRIEVAL_POSTGRES_DSN:
            raise ValueError("RETRIEVAL_BACKEND=postgres requires RETRIEVAL_POSTGRES_DSN to be set.")
        logger.info("Retrieval backend: PostgreSQL full-text search")
        return PostgresRetrievalBackend(
            dsn=config.RETRIEVAL_POSTGRES_DSN,
            language=config.RETRIEVAL_POSTGRES_LANGUAGE,
        )
    logger.info("Retrieval backend: TF-IDF + SQLite")
    from mcp.backends.retrieval.tfidf_sqlite import TfidfSQLiteRetrievalBackend
    return TfidfSQLiteRetrievalBackend(db_path=config.RETRIEVAL_DB_PATH)


def get_retrieval_mcp() -> RetrievalMCP:
    """Return the process-wide RetrievalMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = RetrievalMCP(_create_backend())
    return _instance
