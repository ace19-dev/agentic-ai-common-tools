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
    """

    @abstractmethod
    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult: ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> MCPResult: ...

    @abstractmethod
    def delete(self, doc_id: str) -> MCPResult: ...

    @abstractmethod
    def health_check(self) -> MCPResult: ...
