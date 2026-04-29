from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from core.base_mcp import MCPResult
from mcp.backends.retrieval.base import BaseRetrievalBackend

logger = logging.getLogger(__name__)

_FTS_TABLE = "docs_fts"
_META_TABLE = "docs_meta"


class BM25SQLiteRetrievalBackend(BaseRetrievalBackend):
    """BM25 full-text search backed by SQLite FTS5.

    SQLite FTS5 implements BM25 natively via the ``rank`` virtual column.
    No extra dependencies beyond the Python standard library are required.

    Metadata is stored in a companion table (docs_meta) and joined at query
    time so that metadata_filter can be applied as a SQL pre-filter, keeping
    the result set small before BM25 ranking.

    Corpus size: suitable up to several million documents (SQLite FTS5 scales
    well; the rank column is computed on-the-fly without an in-memory matrix).

    FTS5 tokenizer: unicode61 (default) — handles unicode, case-folding,
    and strips punctuation automatically.
    """

    def __init__(self, db_path: str = "data/retrieval_bm25.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            # FTS5 virtual table — BM25 is the default ranking function
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
                USING fts5(doc_id UNINDEXED, content, tokenize='unicode61')
            """)
            # Companion metadata table
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {_META_TABLE} (
                    doc_id     TEXT PRIMARY KEY,
                    metadata   TEXT NOT NULL DEFAULT '{{}}',
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    # ── metadata filter helper ─────────────────────────────────────────────────

    def _meta_filter_ids(
        self, conn: sqlite3.Connection, metadata_filter: Dict[str, Any]
    ) -> Optional[List[str]]:
        """Return doc_ids that match all metadata equality filters."""
        if not metadata_filter:
            return None
        conditions = [f"json_extract(metadata, '$.{k}') = ?" for k in metadata_filter]
        params = [str(v) for v in metadata_filter.values()]
        sql = f"SELECT doc_id FROM {_META_TABLE} WHERE {' AND '.join(conditions)}"
        try:
            rows = conn.execute(sql, params).fetchall()
            return [r["doc_id"] for r in rows]
        except Exception as exc:
            logger.warning("bm25.metadata pre-filter failed: %s", exc)
            return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            with self._connect() as conn:
                # FTS5 does not support ON CONFLICT — delete then insert
                conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE doc_id = ?", (doc_id,))
                conn.execute(
                    f"INSERT INTO {_FTS_TABLE}(doc_id, content) VALUES (?, ?)",
                    (doc_id, content),
                )
                conn.execute(f"""
                    INSERT INTO {_META_TABLE}(doc_id, metadata, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        metadata   = excluded.metadata,
                        created_at = excluded.created_at
                """, (doc_id, json.dumps(metadata or {}), time.time()))
                conn.commit()
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("bm25.index failed: %s", exc)
            return MCPResult.fail(str(exc))

    def search(self, query: str, top_k: int = 5,
               metadata_filter: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            with self._connect() as conn:
                if metadata_filter:
                    allowed = self._meta_filter_ids(conn, metadata_filter)
                    if allowed is None:
                        # filter failed — fall back to unfiltered
                        allowed_clause = ""
                        params: list = [query, top_k]
                    elif not allowed:
                        return MCPResult.ok(data=[])
                    else:
                        placeholders = ",".join("?" * len(allowed))
                        allowed_clause = f"AND f.doc_id IN ({placeholders})"
                        params = [query] + allowed + [top_k]
                else:
                    allowed_clause = ""
                    params = [query, top_k]

                sql = f"""
                    SELECT f.doc_id, f.content, f.rank, m.metadata
                    FROM {_FTS_TABLE} f
                    JOIN {_META_TABLE} m ON m.doc_id = f.doc_id
                    WHERE {_FTS_TABLE} MATCH ?
                    {allowed_clause}
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, params).fetchall()

            results = [
                {
                    "id": r["doc_id"],
                    "content": r["content"],
                    # FTS5 rank is negative (lower = better); negate for intuitive score
                    "score": round(-float(r["rank"]), 4),
                    "metadata": json.loads(r["metadata"]),
                }
                for r in rows
            ]
            return MCPResult.ok(data=results)
        except Exception as exc:
            logger.error("bm25.search failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, doc_id: str) -> MCPResult:
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    f"DELETE FROM {_FTS_TABLE} WHERE doc_id = ?", (doc_id,)
                )
                conn.execute(f"DELETE FROM {_META_TABLE} WHERE doc_id = ?", (doc_id,))
                conn.commit()
            if cursor.rowcount == 0:
                return MCPResult.fail(f"document '{doc_id}' not found")
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("bm25.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete_chunks(self, source_id: str) -> MCPResult:
        try:
            with self._connect() as conn:
                chunk_ids = conn.execute(
                    f"SELECT doc_id FROM {_META_TABLE} "
                    f"WHERE json_extract(metadata, '$._source_id') = ?",
                    (source_id,),
                ).fetchall()
                ids = [r["doc_id"] for r in chunk_ids]
                if ids:
                    placeholders = ",".join("?" * len(ids))
                    conn.execute(
                        f"DELETE FROM {_FTS_TABLE} WHERE doc_id IN ({placeholders})", ids
                    )
                    conn.execute(
                        f"DELETE FROM {_META_TABLE} WHERE doc_id IN ({placeholders})", ids
                    )
                conn.commit()
            return MCPResult.ok(data=f"deleted: {len(ids)} chunks")
        except Exception as exc:
            logger.error("bm25.delete_chunks failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {_META_TABLE}"
                ).fetchone()[0]
            return MCPResult.ok(data={
                "backend": "bm25_sqlite",
                "doc_count": count,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))
