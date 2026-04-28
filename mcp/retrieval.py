"""
Retrieval MCP — TF-IDF document search backed by SQLite.

Documents are persisted to SQLite for durability.  An in-memory TF-IDF matrix
is rebuilt after every index/delete operation so search results are always
consistent with the stored corpus.

TF-IDF parameters chosen for general-purpose knowledge-base corpora:
  max_features=50_000  — caps vocabulary to bound memory on large corpora
  ngram_range=(1, 2)   — unigrams + bigrams capture short phrases
  sublinear_tf=True    — log(1 + tf) dampens very frequent terms
"""
import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)


class RetrievalMCP(BaseMCP):
    """TF-IDF based semantic retrieval over a SQLite-backed document store.

    Documents are persisted in SQLite. An in-memory TF-IDF index is built on
    startup and rebuilt after every add/delete. Handles empty corpora gracefully.
    Suitable for corpora up to ~100k documents.
    """

    def __init__(self, db_path: str = "data/retrieval.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._vectorizer = None
        self._matrix = None
        self._doc_ids: List[str] = []
        self._init_db()
        self._rebuild_index()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id         TEXT PRIMARY KEY,
                    content    TEXT NOT NULL,
                    metadata   TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _rebuild_index(self) -> None:
        """Re-fit the TF-IDF vectorizer over the entire corpus from SQLite.

        Called after every index() or delete() so the in-memory matrix stays
        consistent with the database.  Sets vectorizer/matrix to None when the
        corpus is empty so search() can return [] without raising an error.
        """
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, content FROM documents ORDER BY created_at"
                ).fetchall()
            self._doc_ids = [r["id"] for r in rows]
            contents = [r["content"] for r in rows]
            if not contents:
                self._vectorizer = None
                self._matrix = None
                return
            self._vectorizer = TfidfVectorizer(
                stop_words="english",
                max_features=50_000,
                ngram_range=(1, 2),
                sublinear_tf=True,
            )
            self._matrix = self._vectorizer.fit_transform(contents)
        except ImportError:
            logger.error("scikit-learn not installed. Run: pip install scikit-learn")
        except Exception as exc:
            logger.error("retrieval._rebuild_index failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO documents (id, content, metadata, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content    = excluded.content,
                        metadata   = excluded.metadata
                """, (doc_id, content, json.dumps(metadata or {}), time.time()))
                conn.commit()
            self._rebuild_index()
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("retrieval.index failed: %s", exc)
            return MCPResult.fail(str(exc))

    def search(self, query: str, top_k: int = 5) -> MCPResult:
        if self._vectorizer is None or self._matrix is None:
            return MCPResult.ok(data=[])
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            query_vec = self._vectorizer.transform([query])
            scores = cosine_similarity(query_vec, self._matrix).flatten()
            top_indices = scores.argsort()[::-1][:top_k]
            results = []
            with self._connect() as conn:
                for idx in top_indices:
                    # Skip documents with zero overlap — they are not relevant
                    # and returning them would pad results with noise.
                    if float(scores[idx]) <= 0.0:
                        continue
                    row = conn.execute(
                        "SELECT id, content, metadata FROM documents WHERE id=?",
                        (self._doc_ids[idx],),
                    ).fetchone()
                    if row:
                        results.append({
                            "id": row["id"],
                            "content": row["content"],
                            "score": round(float(scores[idx]), 4),
                            "metadata": json.loads(row["metadata"]),
                        })
            return MCPResult.ok(data=results)
        except Exception as exc:
            logger.error("retrieval.search failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, doc_id: str) -> MCPResult:
        try:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                conn.commit()
            if cursor.rowcount == 0:
                return MCPResult.fail(f"document '{doc_id}' not found")
            self._rebuild_index()
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("retrieval.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            return MCPResult.ok(data={
                "mcp": "retrieval",
                "doc_count": count,
                "index_ready": self._vectorizer is not None,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[RetrievalMCP] = None


def get_retrieval_mcp() -> RetrievalMCP:
    """Return the process-wide RetrievalMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = RetrievalMCP(db_path=config.RETRIEVAL_DB_PATH)
    return _instance
