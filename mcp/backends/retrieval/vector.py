from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from core.base_mcp import MCPResult
from mcp.backends.retrieval.base import BaseRetrievalBackend

logger = logging.getLogger(__name__)


class VectorRetrievalBackend(BaseRetrievalBackend):
    """Embedding-based semantic search backed by ChromaDB.

    Each document is stored with its embedding computed by ChromaDB's default
    model (all-MiniLM-L6-v2 via sentence-transformers, or a fallback hash-based
    embedding). Semantic similarity is used for ranking instead of TF-IDF.

    Advantages over TF-IDF/SQLite:
      - Handles synonyms and paraphrases (embedding similarity)
      - Scales to millions of documents without in-memory matrix rebuild
      - Persistent: embeddings survive restarts without recomputation

    Requires:
        pip install chromadb>=0.4

    Args:
        path:            Directory for ChromaDB persistent storage.
        collection_name: ChromaDB collection name.
        embedding_fn:    Optional custom chromadb EmbeddingFunction.
    """

    def __init__(
        self,
        path: str = "data/vector_retrieval",
        collection_name: str = "agent_retrieval",
        embedding_fn=None,
    ):
        try:
            import chromadb  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "VectorRetrievalBackend requires chromadb. "
                "Install with: pip install chromadb"
            ) from exc

        import chromadb as _chroma
        self._client = _chroma.PersistentClient(path=path)
        kwargs = {} if embedding_fn is None else {"embedding_function": embedding_fn}
        self._col = self._client.get_or_create_collection(
            name=collection_name, **kwargs
        )

    def index(self, doc_id: str, content: str,
              metadata: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            self._col.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[{**( metadata or {}), "indexed_at": time.time()}],
            )
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("vector.retrieval.index failed: %s", exc)
            return MCPResult.fail(str(exc))

    def search(self, query: str, top_k: int = 5) -> MCPResult:
        try:
            count = self._col.count()
            if count == 0:
                return MCPResult.ok(data=[])
            n_results = min(top_k, count)
            result = self._col.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
            hits = []
            for doc_id, doc, meta, dist in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            ):
                score = round(1.0 - dist, 4)
                if score <= 0.0:
                    continue
                hits.append({
                    "id": doc_id,
                    "content": doc,
                    "score": score,
                    "metadata": {k: v for k, v in meta.items() if k != "indexed_at"},
                })
            return MCPResult.ok(data=hits)
        except Exception as exc:
            logger.error("vector.retrieval.search failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, doc_id: str) -> MCPResult:
        try:
            if not self._col.get(ids=[doc_id])["ids"]:
                return MCPResult.fail(f"document '{doc_id}' not found")
            self._col.delete(ids=[doc_id])
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("vector.retrieval.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            count = self._col.count()
            return MCPResult.ok(data={
                "backend": "vector",
                "collection": self._col.name,
                "doc_count": count,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))
