"""
Memory MCP — persistent key-value store backed by SQLite.

Supports namespaces for logical isolation and optional TTL-based expiry.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import config
from core.base_mcp import BaseMCP, MCPResult
from mcp.backends.memory.base import BaseMemoryBackend

logger = logging.getLogger(__name__)


class MemoryMCP(BaseMCP):
    """Persistent key-value memory store. Delegates all operations to SQLiteMemoryBackend."""

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

    def health_check(self) -> MCPResult:
        result = self._backend.health_check()
        if result.success and isinstance(result.data, dict):
            result.data["mcp"] = "memory"
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[MemoryMCP] = None


def get_memory_mcp() -> MemoryMCP:
    """Return the process-wide MemoryMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        from mcp.backends.memory.sqlite import SQLiteMemoryBackend
        logger.info("Memory backend: SQLite (%s)", config.MEMORY_DB_PATH)
        _instance = MemoryMCP(SQLiteMemoryBackend(db_path=config.MEMORY_DB_PATH))
    return _instance
