from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from core.base_mcp import MCPResult
from mcp.backends.memory.base import BaseMemoryBackend

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend(BaseMemoryBackend):
    """Key-value store backed by a local SQLite file.

    Schema: kv(namespace, key, value, expires_at, created_at, updated_at)
    Primary key is (namespace, key). Expired entries are pruned lazily on get().
    """

    def __init__(self, db_path: str = "data/memory.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
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

    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult:
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
            logger.error("sqlite.memory.set failed: %s", exc)
            return MCPResult.fail(str(exc))

    def get(self, key: str, namespace: str = "default") -> MCPResult:
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
            logger.error("sqlite.memory.get failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, key: str, namespace: str = "default") -> MCPResult:
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
            logger.error("sqlite.memory.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_keys(self, namespace: str = "default") -> MCPResult:
        now = time.time()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key FROM kv WHERE namespace=? AND (expires_at IS NULL OR expires_at > ?)",
                    (namespace, now),
                ).fetchall()
            return MCPResult.ok(data=[r["key"] for r in rows])
        except Exception as exc:
            logger.error("sqlite.memory.list_keys failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return MCPResult.ok(data={"backend": "sqlite", "db": self.db_path})
        except Exception as exc:
            return MCPResult.fail(str(exc))
