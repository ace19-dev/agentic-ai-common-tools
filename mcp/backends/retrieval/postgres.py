from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from core.base_mcp import MCPResult
from mcp.backends.retrieval.base import BaseRetrievalBackend

logger = logging.getLogger(__name__)


class PostgresRetrievalBackend(BaseRetrievalBackend):
    """Full-text search backed by PostgreSQL tsvector/tsquery.

    Documents are stored with a GIN-indexed tsvector column. PostgreSQL's
    ts_rank scoring is used for relevance ordering — no external library needed.

    Advantages over TF-IDF/SQLite:
      - Handles large corpora without in-memory index rebuilds
      - Uses PostgreSQL's built-in stemming and stop-word dictionaries
      - Persistent across process restarts

    Requires:
        pip install psycopg2-binary>=2.9

    Args:
        dsn:      libpq DSN, e.g. "postgresql://user:pw@localhost:5432/db"
        language: PostgreSQL text search configuration (default: "english")
    """

    def __init__(self, dsn: str, language: str = "english"):
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PostgresRetrievalBackend requires psycopg2-binary. "
                "Install with: pip install psycopg2-binary"
            ) from exc
        self.dsn = dsn
        self.language = language
        self._init_db()

    def _connect(self):
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id         TEXT PRIMARY KEY,
                        content    TEXT NOT NULL,
                        metadata   TEXT NOT NULL DEFAULT '{}',
                        tsv        TSVECTOR,
                        created_at DOUBLE PRECISION NOT NULL
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_tsv ON documents USING GIN(tsv)"
                )
                # Trigger keeps tsv column in sync with content automatically
                cur.execute(f"""
                    CREATE OR REPLACE FUNCTION documents_tsv_trigger() RETURNS trigger AS $$
                    BEGIN
                        NEW.tsv := to_tsvector('{self.language}', NEW.content);
                        RETURN NEW;
                    END
                    $$ LANGUAGE plpgsql
                """)
                cur.execute("""
                    DROP TRIGGER IF EXISTS tsvupdate ON documents;
                    CREATE TRIGGER tsvupdate
                        BEFORE INSERT OR UPDATE ON documents
                        FOR EACH ROW EXECUTE FUNCTION documents_tsv_trigger()
                """)
            conn.commit()

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO documents (id, content, metadata, created_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            content    = EXCLUDED.content,
                            metadata   = EXCLUDED.metadata
                    """, (doc_id, content, json.dumps(metadata or {}), time.time()))
                conn.commit()
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("postgres.retrieval.index failed: %s", exc)
            return MCPResult.fail(str(exc))

    def search(self, query: str, top_k: int = 5) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"""
                        SELECT id, content, metadata,
                               ts_rank(tsv, plainto_tsquery('{self.language}', %s)) AS score
                        FROM documents
                        WHERE tsv @@ plainto_tsquery('{self.language}', %s)
                        ORDER BY score DESC
                        LIMIT %s
                    """, (query, query, top_k))
                    rows = cur.fetchall()
            results = [
                {
                    "id": r["id"],
                    "content": r["content"],
                    "score": round(float(r["score"]), 4),
                    "metadata": json.loads(r["metadata"]),
                }
                for r in rows
            ]
            return MCPResult.ok(data=results)
        except Exception as exc:
            logger.error("postgres.retrieval.search failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, doc_id: str) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
                    deleted = cur.rowcount
                conn.commit()
            if deleted == 0:
                return MCPResult.fail(f"document '{doc_id}' not found")
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("postgres.retrieval.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS cnt FROM documents")
                    count = cur.fetchone()["cnt"]
            return MCPResult.ok(data={
                "backend": "postgres",
                "language": self.language,
                "doc_count": count,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))
