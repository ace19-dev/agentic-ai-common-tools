"""
Memory MCP — persistent key-value store with pluggable backends.

Backend is selected via the MEMORY_BACKEND environment variable:
  sqlite   (default) — zero-config, file-backed SQLite store
  postgres           — PostgreSQL; requires MEMORY_POSTGRES_DSN
  vector             — ChromaDB; adds semantic search via memory_search tool

The public API (set/get/delete/list_keys) is identical across all backends.
The vector backend additionally exposes search() for similarity queries.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import config
from core.base_mcp import BaseMCP, MCPResult
from mcp.backends.memory.base import BaseMemoryBackend

logger = logging.getLogger(__name__)


class MemoryMCP(BaseMCP):
    """Persistent key-value memory store. Delegates all operations to a backend.

    Supports namespaces for logical isolation and optional TTL-based expiry.
    The concrete backend is chosen at construction time via MEMORY_BACKEND env var.
    """

    def __init__(self, backend: BaseMemoryBackend):
        self._backend = backend

    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult:
        return self._backend.set(key, value, namespace=namespace, ttl=ttl)

    def get(self, key: str, namespace: str = "default") -> MCPResult:
        return self._backend.get(key, namespace=namespace)

    def delete(self, key: str, namespace: str = "default") -> MCPResult:
        return self._backend.delete(key, namespace=namespace)

    def list_keys(self, namespace: str = "default") -> MCPResult:
        return self._backend.list_keys(namespace=namespace)

    def search(self, query: str, namespace: str = "default", top_k: int = 5) -> MCPResult:
        """Semantic similarity search — only available with MEMORY_BACKEND=vector."""
        if not hasattr(self._backend, "search"):
            return MCPResult.fail(
                "search() requires MEMORY_BACKEND=vector (ChromaDB). "
                f"Current backend: {type(self._backend).__name__}"
            )
        return self._backend.search(query, namespace=namespace, top_k=top_k)

    def health_check(self) -> MCPResult:
        result = self._backend.health_check()
        if result.success and isinstance(result.data, dict):
            result.data["mcp"] = "memory"
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[MemoryMCP] = None


def _create_backend() -> BaseMemoryBackend:
    backend_type = config.MEMORY_BACKEND.lower()
    if backend_type == "postgres":
        from mcp.backends.memory.postgres import PostgresMemoryBackend
        if not config.MEMORY_POSTGRES_DSN:
            raise ValueError("MEMORY_BACKEND=postgres requires MEMORY_POSTGRES_DSN to be set.")
        logger.info("Memory backend: PostgreSQL")
        return PostgresMemoryBackend(dsn=config.MEMORY_POSTGRES_DSN)
    if backend_type == "vector":
        from mcp.backends.memory.vector import VectorMemoryBackend
        logger.info("Memory backend: ChromaDB (vector)")
        return VectorMemoryBackend(
            path=config.MEMORY_VECTOR_PATH,
            collection_name=config.MEMORY_VECTOR_COLLECTION,
        )
    logger.info("Memory backend: SQLite")
    from mcp.backends.memory.sqlite import SQLiteMemoryBackend
    return SQLiteMemoryBackend(db_path=config.MEMORY_DB_PATH)


def get_memory_mcp() -> MemoryMCP:
    """Return the process-wide MemoryMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = MemoryMCP(_create_backend())
    return _instance
