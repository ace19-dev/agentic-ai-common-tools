from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from core.base_mcp import MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ElasticsearchLoggingBackend(BaseLoggingBackend):
    """Elasticsearch(또는 OpenSearch)에 로그를 인덱싱하는 백엔드.

    설정:
        LOGGING_ES_URL=http://localhost:9200
        LOGGING_ES_INDEX=agentic-ai-logs
        LOGGING_ES_API_KEY=<base64 encoded api key>   # 선택사항

    인덱스 매핑은 첫 문서 삽입 시 ES가 자동 생성합니다(dynamic mapping).
    query/tail은 ES의 /_search API를 사용합니다.
    """

    def __init__(self,
                 url: str,
                 index: str = "agentic-ai-logs",
                 api_key: str = "",
                 timeout: int = 10):
        self.base_url = url.rstrip("/")
        self.index = index
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"ApiKey {api_key}"

    def _index_url(self) -> str:
        return f"{self.base_url}/{self.index}/_doc"

    def _search_url(self) -> str:
        return f"{self.base_url}/{self.index}/_search"

    def _delete_url(self) -> str:
        return f"{self.base_url}/{self.index}/_delete_by_query"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_hit(hit: dict) -> dict:
        src = hit.get("_source", {})
        return {
            "id":        hit.get("_id"),
            "timestamp": src.get("timestamp", ""),
            "level":     src.get("level", ""),
            "source":    src.get("source", ""),
            "message":   src.get("message", ""),
            "metadata":  src.get("metadata", {}),
        }

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"
        doc = {
            "timestamp": self._now_iso(),
            "level":     lvl,
            "source":    source,
            "message":   message,
            "metadata":  metadata or {},
        }
        try:
            resp = requests.post(
                self._index_url(),
                headers=self._headers,
                data=json.dumps(doc, default=str),
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                return MCPResult.ok(data="logged")
            return MCPResult.fail(f"ES index 실패: HTTP {resp.status_code} — {resp.text[:300]}")
        except Exception as exc:
            logger.error("elasticsearch.logging.write 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        must: list[dict] = []
        if level:
            must.append({"term": {"level": level.upper()}})
        if source:
            must.append({"term": {"source": source}})

        range_filter: dict = {}
        if since:
            range_filter["gte"] = since
        if until:
            range_filter["lte"] = until
        if range_filter:
            must.append({"range": {"timestamp": range_filter}})

        body = {
            "query":  {"bool": {"must": must}} if must else {"match_all": {}},
            "sort":   [{"timestamp": {"order": "asc"}}],
            "size":   min(limit, 1000),
        }
        try:
            resp = requests.get(
                self._search_url(),
                headers=self._headers,
                data=json.dumps(body),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return MCPResult.fail(f"ES search 실패: HTTP {resp.status_code} — {resp.text[:300]}")
            hits = resp.json().get("hits", {}).get("hits", [])
            return MCPResult.ok(data=[self._parse_hit(h) for h in hits])
        except Exception as exc:
            logger.error("elasticsearch.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        must: list[dict] = []
        if source:
            must.append({"term": {"source": source}})
        body = {
            "query":  {"bool": {"must": must}} if must else {"match_all": {}},
            "sort":   [{"timestamp": {"order": "desc"}}],
            "size":   max(1, min(n, 500)),
        }
        try:
            resp = requests.get(
                self._search_url(),
                headers=self._headers,
                data=json.dumps(body),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return MCPResult.fail(f"ES tail 실패: HTTP {resp.status_code} — {resp.text[:300]}")
            hits = resp.json().get("hits", {}).get("hits", [])
            return MCPResult.ok(data=list(reversed([self._parse_hit(h) for h in hits])))
        except Exception as exc:
            logger.error("elasticsearch.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        must: list[dict] = []
        if before:
            must.append({"range": {"timestamp": {"lt": before}}})
        if source:
            must.append({"term": {"source": source}})

        body = {
            "query": {"bool": {"must": must}} if must else {"match_all": {}}
        }
        try:
            resp = requests.post(
                self._delete_url(),
                headers=self._headers,
                data=json.dumps(body),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                deleted = resp.json().get("deleted", 0)
                return MCPResult.ok(data={"deleted": deleted})
            return MCPResult.fail(f"ES delete_by_query 실패: HTTP {resp.status_code} — {resp.text[:300]}")
        except Exception as exc:
            logger.error("elasticsearch.logging.clear 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            resp = requests.get(
                f"{self.base_url}/_cluster/health",
                headers=self._headers,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return MCPResult.ok(data={
                    "backend": "elasticsearch",
                    "url":     self.base_url,
                    "index":   self.index,
                    "status":  data.get("status", "unknown"),
                })
            return MCPResult.fail(f"ES health 실패: HTTP {resp.status_code}")
        except Exception as exc:
            return MCPResult.fail(f"Elasticsearch 연결 실패: {exc}")
