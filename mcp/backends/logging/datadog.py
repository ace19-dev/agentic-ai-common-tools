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

# Datadog 사이트별 엔드포인트
_INTAKE_HOSTS = {
    "datadoghq.com":    "http-intake.logs.datadoghq.com",
    "datadoghq.eu":     "http-intake.logs.datadoghq.eu",
    "us3.datadoghq.com": "http-intake.logs.us3.datadoghq.com",
    "us5.datadoghq.com": "http-intake.logs.us5.datadoghq.com",
    "ap1.datadoghq.com": "http-intake.logs.ap1.datadoghq.com",
}
_API_HOSTS = {
    "datadoghq.com":    "api.datadoghq.com",
    "datadoghq.eu":     "api.datadoghq.eu",
    "us3.datadoghq.com": "api.us3.datadoghq.com",
    "us5.datadoghq.com": "api.us5.datadoghq.com",
    "ap1.datadoghq.com": "api.ap1.datadoghq.com",
}


class DatadogLoggingBackend(BaseLoggingBackend):
    """Datadog Logs API를 사용하는 로깅 백엔드.

    설정:
        LOGGING_DATADOG_API_KEY=<dd-api-key>
        LOGGING_DATADOG_APP_KEY=<dd-app-key>   # query/tail에 필요
        LOGGING_DATADOG_SITE=datadoghq.com     # 또는 datadoghq.eu 등
        LOGGING_DATADOG_SERVICE=agentic-ai
        LOGGING_DATADOG_SOURCE=python

    write → POST /api/v2/logs  (API key만 필요)
    query → POST /api/v2/logs/events/search  (API key + Application key 필요)
    tail  → query와 동일, 최신순 정렬
    clear → Datadog 로그는 불변(immutable)이므로 지원하지 않음
    """

    def __init__(self,
                 api_key: str,
                 app_key: str = "",
                 site: str = "datadoghq.com",
                 service: str = "agentic-ai",
                 source: str = "python",
                 timeout: int = 10):
        self.api_key = api_key
        self.app_key = app_key
        self.site = site
        self.service = service
        self.source = source
        self.timeout = timeout

        intake_host = _INTAKE_HOSTS.get(site, f"http-intake.logs.{site}")
        api_host = _API_HOSTS.get(site, f"api.{site}")
        self._intake_url = f"https://{intake_host}/api/v2/logs"
        self._search_url = f"https://{api_host}/api/v2/logs/events/search"
        self._validate_url = f"https://{api_host}/api/v1/validate"

    def _intake_headers(self) -> dict:
        return {
            "DD-API-KEY":    self.api_key,
            "Content-Type":  "application/json",
        }

    def _search_headers(self) -> dict:
        return {
            "DD-API-KEY":          self.api_key,
            "DD-APPLICATION-KEY":  self.app_key,
            "Content-Type":        "application/json",
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"

        payload = [{
            "ddsource":  self.source,
            "service":   self.service,
            "status":    lvl.lower(),       # Datadog은 소문자 status 선호
            "message":   message,
            "ddtags":    f"source:{source},level:{lvl}" if source else f"level:{lvl}",
            "timestamp": self._now_iso(),
            **({"metadata": metadata} if metadata else {}),
        }]
        try:
            resp = requests.post(
                self._intake_url,
                headers=self._intake_headers(),
                data=json.dumps(payload, default=str),
                timeout=self.timeout,
            )
            if resp.status_code in (200, 202):
                return MCPResult.ok(data="logged")
            return MCPResult.fail(
                f"Datadog intake 실패: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        except Exception as exc:
            logger.error("datadog.logging.write 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def _build_search_body(self,
                           level: Optional[str],
                           source: Optional[str],
                           since: Optional[str],
                           until: Optional[str],
                           limit: int,
                           sort: str = "timestamp") -> dict:
        query_parts = [f"service:{self.service}"]
        if level:
            query_parts.append(f"status:{level.lower()}")
        if source:
            query_parts.append(f"source:{source}")

        body: dict = {
            "filter": {
                "query": " ".join(query_parts),
                "from":  since or "now-1h",
                "to":    until or "now",
            },
            "sort": sort,
            "page": {"limit": min(limit, 1000)},
        }
        return body

    def _parse_events(self, data: dict) -> list[dict]:
        entries = []
        for event in data.get("data", []):
            attrs = event.get("attributes", {})
            tags: str = attrs.get("tags", "")
            # source 태그 파싱
            src = ""
            for tag in (tags.split(",") if tags else []):
                if tag.startswith("source:"):
                    src = tag.split(":", 1)[1]
                    break
            entries.append({
                "id":        event.get("id"),
                "timestamp": attrs.get("timestamp", ""),
                "level":     attrs.get("status", "INFO").upper(),
                "source":    src,
                "message":   attrs.get("message", ""),
                "metadata":  attrs.get("attributes", {}).get("metadata", {}),
            })
        return entries

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        if not self.app_key:
            return MCPResult.fail(
                "query/tail에는 LOGGING_DATADOG_APP_KEY(Application Key)가 필요합니다."
            )
        body = self._build_search_body(level, source, since, until, limit, sort="timestamp")
        try:
            resp = requests.post(
                self._search_url,
                headers=self._search_headers(),
                data=json.dumps(body),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return MCPResult.fail(
                    f"Datadog search 실패: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            return MCPResult.ok(data=self._parse_events(resp.json()))
        except Exception as exc:
            logger.error("datadog.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        if not self.app_key:
            return MCPResult.fail(
                "query/tail에는 LOGGING_DATADOG_APP_KEY(Application Key)가 필요합니다."
            )
        body = self._build_search_body(
            level=None, source=source,
            since=None, until=None,
            limit=max(1, min(n, 500)),
            sort="-timestamp",   # 최신순
        )
        try:
            resp = requests.post(
                self._search_url,
                headers=self._search_headers(),
                data=json.dumps(body),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return MCPResult.fail(
                    f"Datadog tail 실패: HTTP {resp.status_code} — {resp.text[:300]}"
                )
            entries = self._parse_events(resp.json())
            return MCPResult.ok(data=list(reversed(entries)))  # 오래된 것부터 정렬
        except Exception as exc:
            logger.error("datadog.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        return MCPResult.fail(
            "Datadog 로그는 불변(immutable)입니다. "
            "데이터 보존 정책은 Datadog 콘솔 → Logs → Configuration → Archives에서 관리하세요."
        )

    def health_check(self) -> MCPResult:
        try:
            resp = requests.get(
                self._validate_url,
                headers={"DD-API-KEY": self.api_key},
                timeout=self.timeout,
            )
            if resp.status_code == 200 and resp.json().get("valid"):
                return MCPResult.ok(data={
                    "backend": "datadog",
                    "site":    self.site,
                    "service": self.service,
                    "valid":   True,
                })
            return MCPResult.fail(
                f"Datadog API key 유효하지 않음: HTTP {resp.status_code}"
            )
        except Exception as exc:
            return MCPResult.fail(f"Datadog 연결 실패: {exc}")
