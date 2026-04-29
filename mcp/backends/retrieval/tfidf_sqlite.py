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


class TfidfSQLiteRetrievalBackend(BaseRetrievalBackend):
    """TF-IDF cosine similarity search over a SQLite-backed document store.

    Documents are persisted in SQLite. An in-memory TF-IDF index is rebuilt
    after every index/delete operation. Suitable for corpora up to ~100k docs.

    TF-IDF parameters:
      max_features=50_000 — caps vocabulary to bound memory on large corpora
      ngram_range=(1, 2)  — unigrams + bigrams capture short phrases
      sublinear_tf=True   — log(1 + tf) dampens very frequent terms

    Requires: pip install scikit-learn
    """

    def __init__(self, db_path: str = "data/retrieval.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._vectorizer = None
        self._matrix = None
        self._doc_ids: List[str] = []
        self._init_db()
        self._rebuild_index()

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
            logger.error("tfidf._rebuild_index failed: %s", exc)

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO documents (id, content, metadata, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content  = excluded.content,
                        metadata = excluded.metadata
                """, (doc_id, content, json.dumps(metadata or {}), time.time()))
                conn.commit()
            self._rebuild_index()
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("tfidf.index failed: %s", exc)
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
            logger.error("tfidf.search failed: %s", exc)
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
            logger.error("tfidf.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            return MCPResult.ok(data={
                "backend": "tfidf_sqlite",
                "doc_count": count,
                "index_ready": self._vectorizer is not None,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))
