from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from core.base_mcp import MCPResult
from mcp.backends.memory.base import BaseMemoryBackend

logger = logging.getLogger(__name__)

_NO_EXPIRY = -1.0  # ChromaDB metadata cannot store None; use sentinel float


class VectorMemoryBackend(BaseMemoryBackend):
    """Key-value store backed by ChromaDB with semantic search support.

    Each entry is stored as a ChromaDB document:
      id       = "{namespace}::{key}"
      document = JSON-serialised value (embedded by ChromaDB)
      metadata = {"namespace": str, "key": str, "expires_at": float}

    The base KV interface (get/set/delete/list_keys) uses exact id lookup.
    The additional search() method performs semantic similarity search.

    Requires:
        pip install chromadb>=0.4

    Args:
        path:            Directory for ChromaDB persistent storage.
        collection_name: ChromaDB collection name.
        embedding_fn:    Optional custom chromadb EmbeddingFunction.
                         Uses ChromaDB's default (all-MiniLM-L6-v2) when None.
    """

    def __init__(
        self,
        path: str = "data/vector_memory",
        collection_name: str = "agent_memory",
        embedding_fn=None,
    ):
        try:
            import chromadb  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "VectorMemoryBackend requires chromadb. "
                "Install with: pip install chromadb"
            ) from exc

        import chromadb as _chroma
        self._client = _chroma.PersistentClient(path=path)
        kwargs = {} if embedding_fn is None else {"embedding_function": embedding_fn}
        self._col = self._client.get_or_create_collection(
            name=collection_name, **kwargs
        )

    @staticmethod
    def _doc_id(namespace: str, key: str) -> str:
        return f"{namespace}::{key}"

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at != _NO_EXPIRY and expires_at < time.time()

    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult:
        expires_at = (time.time() + ttl) if ttl and ttl > 0 else _NO_EXPIRY
        try:
            doc = json.dumps(value, default=str, ensure_ascii=False)
            self._col.upsert(
                ids=[self._doc_id(namespace, key)],
                documents=[doc],
                metadatas=[{"namespace": namespace, "key": key, "expires_at": expires_at}],
            )
            return MCPResult.ok(data="stored")
        except Exception as exc:
            logger.error("vector.memory.set failed: %s", exc)
            return MCPResult.fail(str(exc))

    def get(self, key: str, namespace: str = "default") -> MCPResult:
        try:
            result = self._col.get(
                ids=[self._doc_id(namespace, key)],
                include=["documents", "metadatas"],
            )
            if not result["ids"]:
                return MCPResult.fail(f"key '{key}' not found in namespace '{namespace}'")
            meta = result["metadatas"][0]
            if self._is_expired(meta.get("expires_at", _NO_EXPIRY)):
                self.delete(key, namespace)
                return MCPResult.fail(f"key '{key}' has expired")
            return MCPResult.ok(data=json.loads(result["documents"][0]))
        except Exception as exc:
            logger.error("vector.memory.get failed: %s", exc)
            return MCPResult.fail(str(exc))

    def delete(self, key: str, namespace: str = "default") -> MCPResult:
        try:
            doc_id = self._doc_id(namespace, key)
            if not self._col.get(ids=[doc_id])["ids"]:
                return MCPResult.fail(f"key '{key}' not found")
            self._col.delete(ids=[doc_id])
            return MCPResult.ok(data="deleted")
        except Exception as exc:
            logger.error("vector.memory.delete failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_keys(self, namespace: str = "default") -> MCPResult:
        now = time.time()
        try:
            result = self._col.get(
                where={"namespace": {"$eq": namespace}},
                include=["metadatas"],
            )
            keys = [
                m["key"] for m in result["metadatas"]
                if m.get("expires_at", _NO_EXPIRY) == _NO_EXPIRY
                or m["expires_at"] > now
            ]
            return MCPResult.ok(data=keys)
        except Exception as exc:
            logger.error("vector.memory.list_keys failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            count = self._col.count()
            return MCPResult.ok(data={
                "backend": "vector",
                "collection": self._col.name,
                "document_count": count,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))

    def search(self, query: str, namespace: str = "default", top_k: int = 5) -> MCPResult:
        """Semantic similarity search over all documents in a namespace.

        Args:
            query:     Natural language search query (embedded by ChromaDB).
            namespace: Restrict search to this namespace.
            top_k:     Maximum number of results to return.

        Returns:
            MCPResult.data = list of {"key", "value", "score", "namespace"}.
        """
        now = time.time()
        try:
            result = self._col.query(
                query_texts=[query],
                n_results=top_k,
                where={"namespace": {"$eq": namespace}},
                include=["documents", "metadatas", "distances"],
            )
            hits = []
            for doc, meta, dist in zip(
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            ):
                if self._is_expired(meta.get("expires_at", _NO_EXPIRY)):
                    continue
                hits.append({
                    "key": meta["key"],
                    "namespace": meta["namespace"],
                    "value": json.loads(doc),
                    "score": round(1.0 - dist, 4),
                })
            return MCPResult.ok(data=hits)
        except Exception as exc:
            logger.error("vector.memory.search failed: %s", exc)
            return MCPResult.fail(str(exc))
