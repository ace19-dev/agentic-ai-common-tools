from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from core.base_mcp import MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class LokiLoggingBackend(BaseLoggingBackend):
    """Grafana Loki HTTP Push API를 사용하는 로깅 백엔드.

    설정:
        LOGGING_LOKI_URL=http://localhost:3100
        LOGGING_LOKI_LABELS={"app": "agentic-ai", "env": "dev"}

    Push API: POST /loki/api/v1/push
    Query API: GET  /loki/api/v1/query_range  (query/tail에서 사용)

    query/tail은 LogQL을 사용합니다. Loki가 미설정이거나 응답하지 않으면
    MCPResult.fail()을 반환해 다른 백엔드와 동일하게 동작합니다.
    """

    def __init__(self, url: str, labels: Optional[dict] = None, timeout: int = 5):
        self.base_url = url.rstrip("/")
        self.labels = labels or {"app": "agentic-ai"}
        self.timeout = timeout

    def _push_url(self) -> str:
        return f"{self.base_url}/loki/api/v1/push"

    def _query_url(self) -> str:
        return f"{self.base_url}/loki/api/v1/query_range"

    @staticmethod
    def _now_ns() -> str:
        """현재 시각을 Loki가 요구하는 나노초 유닉스 타임스탬프 문자열로 반환합니다."""
        return str(int(time.time() * 1e9))

    @staticmethod
    def _iso_to_ns(iso: str) -> str:
        """ISO 8601 문자열을 나노초 유닉스 타임스탬프 문자열로 변환합니다."""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return str(int(dt.timestamp() * 1e9))
        except Exception:
            return str(int(time.time() * 1e9))

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"

        stream_labels = dict(self.labels)
        stream_labels["level"] = lvl
        if source:
            stream_labels["source"] = source

        log_line = json.dumps({
            "message":  message,
            "metadata": metadata or {},
        }, ensure_ascii=False, default=str)

        payload = {
            "streams": [{
                "stream": stream_labels,
                "values": [[self._now_ns(), log_line]],
            }]
        }
        try:
            resp = requests.post(
                self._push_url(),
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if resp.status_code in (200, 204):
                return MCPResult.ok(data="logged")
            return MCPResult.fail(f"Loki push 실패: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as exc:
            logger.error("loki.logging.write 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def _logql_query(self,
                     level: Optional[str] = None,
                     source: Optional[str] = None) -> str:
        """LogQL 스트림 선택기를 생성합니다."""
        selectors = dict(self.labels)
        if level:
            selectors["level"] = level.upper()
        if source:
            selectors["source"] = source
        parts = ", ".join(f'{k}="{v}"' for k, v in selectors.items())
        return "{" + parts + "}"

    def _parse_loki_response(self, data: dict) -> list[dict]:
        entries: list[dict] = []
        for result in data.get("data", {}).get("result", []):
            stream = result.get("stream", {})
            for ts_ns, line in result.get("values", []):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    parsed = {"message": line}
                ts_sec = int(ts_ns) / 1e9
                entries.append({
                    "id":        None,
                    "timestamp": datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat(),
                    "level":     stream.get("level", "INFO"),
                    "source":    stream.get("source", ""),
                    "message":   parsed.get("message", line),
                    "metadata":  parsed.get("metadata", {}),
                })
        return sorted(entries, key=lambda e: e["timestamp"])

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        logql = self._logql_query(level, source)
        params = {
            "query": logql,
            "limit": min(limit, 1000),
            "start": self._iso_to_ns(since) if since else str(int((time.time() - 3600) * 1e9)),
            "end":   self._iso_to_ns(until) if until else self._now_ns(),
            "direction": "forward",
        }
        try:
            resp = requests.get(self._query_url(), params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return MCPResult.fail(f"Loki query 실패: HTTP {resp.status_code} — {resp.text[:200]}")
            return MCPResult.ok(data=self._parse_loki_response(resp.json()))
        except Exception as exc:
            logger.error("loki.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        logql = self._logql_query(source=source)
        params = {
            "query":     logql,
            "limit":     max(1, min(n, 500)),
            "start":     str(int((time.time() - 86400) * 1e9)),  # 최대 24시간 이내
            "end":       self._now_ns(),
            "direction": "backward",
        }
        try:
            resp = requests.get(self._query_url(), params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return MCPResult.fail(f"Loki tail 실패: HTTP {resp.status_code} — {resp.text[:200]}")
            entries = self._parse_loki_response(resp.json())
            return MCPResult.ok(data=entries[-n:])
        except Exception as exc:
            logger.error("loki.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        # Loki는 삭제 API가 별도 활성화가 필요하며 일반적으로 지원되지 않음
        return MCPResult.fail(
            "Loki 백엔드는 clear를 지원하지 않습니다. "
            "Loki의 retention 정책(chunk_store_config) 또는 "
            "compactor를 사용해 데이터를 관리하세요."
        )

    def health_check(self) -> MCPResult:
        try:
            resp = requests.get(f"{self.base_url}/ready", timeout=self.timeout)
            if resp.status_code == 200:
                return MCPResult.ok(data={"backend": "loki", "url": self.base_url, "status": "ready"})
            return MCPResult.fail(f"Loki not ready: HTTP {resp.status_code}")
        except Exception as exc:
            return MCPResult.fail(f"Loki 연결 실패: {exc}")
