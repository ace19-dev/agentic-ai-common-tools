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
    embedding). ChromaDB's native `where` filtering is used for metadata queries.

    Advantages over TF-IDF:
      - Handles synonyms and paraphrases (embedding similarity)
      - Scales without in-memory index rebuilds
      - Embeddings persist across restarts

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
            # ChromaDB metadata values must be str/int/float/bool
            safe_meta = {k: (str(v) if not isinstance(v, (str, int, float, bool)) else v)
                         for k, v in (metadata or {}).items()}
            safe_meta["indexed_at"] = time.time()
            self._col.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[safe_meta],
            )
            return MCPResult.ok(data="indexed")
        except Exception as exc:
            logger.error("vector.retrieval.index failed: %s", exc)
            return MCPResult.fail(str(exc))

    def search(self, query: str, top_k: int = 5,
               metadata_filter: Optional[Dict[str, Any]] = None) -> MCPResult:
        try:
            count = self._col.count()
            if count == 0:
                return MCPResult.ok(data=[])
            n_results = min(top_k, count)
            kwargs: dict = {
                "query_texts": [query],
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if metadata_filter:
                # Build ChromaDB $eq where clause
                if len(metadata_filter) == 1:
                    key, val = next(iter(metadata_filter.items()))
                    kwargs["where"] = {key: {"$eq": str(val)}}
                else:
                    kwargs["where"] = {"$and": [
                        {k: {"$eq": str(v)}} for k, v in metadata_filter.items()
                    ]}
            result = self._col.query(**kwargs)
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

    def delete_chunks(self, source_id: str) -> MCPResult:
        try:
            existing = self._col.get(
                where={"_source_id": {"$eq": source_id}},
                include=["metadatas"],
            )
            count = len(existing["ids"])
            if count > 0:
                self._col.delete(ids=existing["ids"])
            return MCPResult.ok(data=f"deleted: {count} chunks")
        except Exception as exc:
            logger.error("vector.retrieval.delete_chunks failed: %s", exc)
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
