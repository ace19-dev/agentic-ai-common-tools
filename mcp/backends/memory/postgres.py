from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from core.base_mcp import MCPResult
from mcp.backends.memory.base import BaseMemoryBackend

logger = logging.getLogger(__name__)


class PostgresMemoryBackend(BaseMemoryBackend):
    """Key-value store backed by PostgreSQL.

    Uses the same logical schema as SQLiteMemoryBackend so data can be migrated
    between backends without schema changes.

    Requires:
        pip install psycopg2-binary>=2.9

    Args:
        dsn: libpq connection string, e.g.
             "postgresql://user:password@localhost:5432/dbname"
    """

    def __init__(self, dsn: str):
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PostgresMemoryBackend requires psycopg2-binary. "
                "Install with: pip install psycopg2-binary"
            ) from exc
        self.dsn = dsn
        self._init_db()

    def _connect(self):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS kv (
                        namespace  TEXT NOT NULL DEFAULT 'default',
                        key        TEXT NOT NULL,
                        value      TEXT NOT NULL,
                        expires_at DOUBLE PRECISION,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY (namespace, key)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at)"
                )
            conn.commit()

    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult:
        now = time.time()
        expires_at = (now + ttl) if ttl and ttl > 0 else None
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO kv (namespace, key, value, expires_at, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (namespace, key) DO UPDATE SET
                            value      = EXCLUDED.value,
                            expires_at = EXCLUDED.expires_at,
                            updated_at = EXCLUDED.updated_at
                    """, (namespace, key, json.dumps(value, default=str), expires_at, now, now))
                conn.commit()
            return MCPResult.ok(data="stored")
        except Exception as exc:
            logger.error("postgres.memory.set failed: %s", exc)
            return MCPResult.fail(str(exc))

    def get(self, key: str, namespace: str = "default") -> MCPResult:
        now = time.time()
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT value, expires_at FROM kv WHERE namespace=%s AND key=%s",
                        (namespace, key),
                    )
                    row = cur.fetchone()
            if row is None:
                return MCPResult.fail(f"key '{key}' not found in namespace '{namespace}'")
            expires_at = row["expires_at"]
            if expires_at is not None and expires_at < now:
                self.delete(key, namespace)
                return MCPResult.fail(f"key '{key}' has expired")
            return MCPResult.ok(data=json.loads(row["value"]))
        except Exception as exc:
            logger.error("postgres.memory.get failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, key: str, namespace: str = "default") -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM kv WHERE namespace=%s AND key=%s", (namespace, key)
                    )
                    deleted = cur.rowcount
                conn.commit()
            if deleted == 0:
                return MCPResult.fail(f"key '{key}' not found")
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("postgres.memory.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_keys(self, namespace: str = "default") -> MCPResult:
        now = time.time()
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT key FROM kv WHERE namespace=%s "
                        "AND (expires_at IS NULL OR expires_at > %s)",
                        (namespace, now),
                    )
                    rows = cur.fetchall()
            return MCPResult.ok(data=[r["key"] for r in rows])
        except Exception as exc:
            logger.error("postgres.memory.list_keys failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return MCPResult.ok(data={"backend": "postgres"})
        except Exception as exc:
            return MCPResult.fail(str(exc))
