"""
Logging MCP — 구조화 로그 기록 및 조회.

플러그인 가능한 백엔드:
  sqlite        (기본값) — 로컬 SQLite DB, 설정 없이 즉시 사용
  file          — 회전(rotating) JSON Lines 파일
  loki          — Grafana Loki HTTP Push API
  elasticsearch — Elasticsearch / OpenSearch
  datadog       — Datadog Logs API
  postgres      — PostgreSQL (psycopg2-binary 필요)

환경 변수로 백엔드를 선택합니다:
  LOGGING_BACKEND=sqlite   # sqlite | file | loki | elasticsearch | datadog | postgres
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from core.base_mcp import BaseMCP, MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)


class LoggingMCP(BaseMCP):
    """구조화 로그 저장소. 모든 연산을 백엔드로 위임합니다."""

    def __init__(self, backend: BaseLoggingBackend):
        self._backend = backend

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        """로그 엔트리 1건을 기록합니다."""
        return self._backend.write(level, message, source=source, metadata=metadata)

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        """조건으로 로그를 검색합니다. 결과는 timestamp 오름차순입니다."""
        return self._backend.query(
            level=level, source=source,
            since=since, until=until, limit=limit,
        )

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        """가장 최근 n개의 로그 엔트리를 반환합니다."""
        return self._backend.tail(n=n, source=source)

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        """로그를 삭제합니다. before(ISO 문자열) 이전 항목만 삭제하거나 전체 삭제."""
        return self._backend.clear(before=before, source=source)

    def health_check(self) -> MCPResult:
        result = self._backend.health_check()
        if result.success and isinstance(result.data, dict):
            result.data["mcp"] = "logging"
        return result


# ── 모듈 수준 싱글턴 ──────────────────────────────────────────────────────────

_instance: Optional[LoggingMCP] = None


def get_logging_mcp() -> LoggingMCP:
    """프로세스 전역 LoggingMCP 싱글턴을 반환합니다. 최초 호출 시 생성됩니다."""
    global _instance
    if _instance is None:
        backend_name = config.LOGGING_BACKEND.lower()

        if backend_name == "file":
            from mcp.backends.logging.file import FileLoggingBackend
            logger.info("Logging 백엔드: File (%s)", config.LOGGING_FILE_PATH)
            backend: BaseLoggingBackend = FileLoggingBackend(
                log_path=config.LOGGING_FILE_PATH,
                max_bytes=config.LOGGING_FILE_MAX_BYTES,
                backup_count=config.LOGGING_FILE_BACKUP_COUNT,
            )

        elif backend_name == "loki":
            from mcp.backends.logging.loki import LokiLoggingBackend
            import json as _json
            labels = {}
            try:
                labels = _json.loads(config.LOGGING_LOKI_LABELS)
            except Exception:
                pass
            logger.info("Logging 백엔드: Loki (%s)", config.LOGGING_LOKI_URL)
            backend = LokiLoggingBackend(
                url=config.LOGGING_LOKI_URL,
                labels=labels,
            )

        elif backend_name == "elasticsearch":
            from mcp.backends.logging.elasticsearch import ElasticsearchLoggingBackend
            logger.info("Logging 백엔드: Elasticsearch (%s)", config.LOGGING_ES_URL)
            backend = ElasticsearchLoggingBackend(
                url=config.LOGGING_ES_URL,
                index=config.LOGGING_ES_INDEX,
                api_key=config.LOGGING_ES_API_KEY,
            )

        elif backend_name == "datadog":
            from mcp.backends.logging.datadog import DatadogLoggingBackend
            logger.info("Logging 백엔드: Datadog (site=%s)", config.LOGGING_DATADOG_SITE)
            backend = DatadogLoggingBackend(
                api_key=config.LOGGING_DATADOG_API_KEY,
                app_key=config.LOGGING_DATADOG_APP_KEY,
                site=config.LOGGING_DATADOG_SITE,
                service=config.LOGGING_DATADOG_SERVICE,
                source=config.LOGGING_DATADOG_SOURCE,
            )

        elif backend_name == "postgres":
            from mcp.backends.logging.postgres import PostgresLoggingBackend
            logger.info("Logging 백엔드: PostgreSQL (table=%s)", config.LOGGING_POSTGRES_TABLE)
            backend = PostgresLoggingBackend(
                dsn=config.LOGGING_POSTGRES_DSN,
                table=config.LOGGING_POSTGRES_TABLE,
            )

        else:
            from mcp.backends.logging.sqlite import SQLiteLoggingBackend
            logger.info("Logging 백엔드: SQLite (%s)", config.LOGGING_DB_PATH)
            backend = SQLiteLoggingBackend(db_path=config.LOGGING_DB_PATH)

        _instance = LoggingMCP(backend)
    return _instance
