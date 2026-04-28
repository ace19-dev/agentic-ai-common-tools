"""
Memory MCP — persistent key-value store backed by SQLite.

Schema: kv(namespace TEXT, key TEXT, value TEXT, expires_at REAL,
            created_at REAL, updated_at REAL)
PRIMARY KEY is (namespace, key), so the same key can exist in multiple
namespaces without collision.  Expired entries are pruned lazily on get()
rather than by a background sweep to avoid the need for a second thread.
"""
import json
import logging
import os
import sqlite3
import time
from typing import Any, List, Optional

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)


class MemoryMCP(BaseMCP):
    """Persistent key-value memory store backed by SQLite.

    Supports namespaces for logical isolation and optional TTL-based expiry.
    Each (namespace, key) pair is an independent record; expired entries are
    lazily pruned on read.
    """

    def __init__(self, db_path: str = "data/memory.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection; row_factory enables column-name access."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    namespace  TEXT NOT NULL DEFAULT 'default',
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    expires_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at)")
            conn.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult:
        """Store or update a value.  ON CONFLICT performs an upsert in a single statement."""
        now = time.time()
        expires_at = (now + ttl) if ttl and ttl > 0 else None
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO kv (namespace, key, value, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value      = excluded.value,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                """, (namespace, key, json.dumps(value, default=str), expires_at, now, now))
                conn.commit()
            return MCPResult.ok(data="stored")
        except Exception as exc:
            logger.error("memory.set failed: %s", exc)
            return MCPResult.fail(str(exc))

    def get(self, key: str, namespace: str = "default") -> MCPResult:
        """Retrieve a value, returning fail() if the key is absent or has expired."""
        now = time.time()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value, expires_at FROM kv WHERE namespace=? AND key=?",
                    (namespace, key),
                ).fetchone()
            if row is None:
                return MCPResult.fail(f"key '{key}' not found in namespace '{namespace}'")
            expires_at = row["expires_at"]
            if expires_at is not None and expires_at < now:
                self.delete(key, namespace)
                return MCPResult.fail(f"key '{key}' has expired")
            return MCPResult.ok(data=json.loads(row["value"]))
        except Exception as exc:
            logger.error("memory.get failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, key: str, namespace: str = "default") -> MCPResult:
        """Delete a key, returning fail() if it did not exist."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM kv WHERE namespace=? AND key=?", (namespace, key)
                )
                conn.commit()
            if cursor.rowcount == 0:
                return MCPResult.fail(f"key '{key}' not found")
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("memory.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_keys(self, namespace: str = "default") -> MCPResult:
        """Return all live (non-expired) keys in a namespace."""
        now = time.time()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key FROM kv WHERE namespace=? AND (expires_at IS NULL OR expires_at > ?)",
                    (namespace, now),
                ).fetchall()
            return MCPResult.ok(data=[r["key"] for r in rows])
        except Exception as exc:
            logger.error("memory.list_keys failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return MCPResult.ok(data={"mcp": "memory", "backend": "sqlite", "db": self.db_path})
        except Exception as exc:
            return MCPResult.fail(str(exc))


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[MemoryMCP] = None


def get_memory_mcp() -> MemoryMCP:
    """Return the process-wide MemoryMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = MemoryMCP(db_path=config.MEMORY_DB_PATH)
    return _instance
