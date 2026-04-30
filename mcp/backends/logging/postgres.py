from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from core.base_mcp import MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class PostgresLoggingBackend(BaseLoggingBackend):
    """PostgreSQL에 구조화 로그를 저장하는 백엔드.

    설정:
        LOGGING_POSTGRES_DSN=postgresql://user:password@localhost:5432/dbname
        LOGGING_POSTGRES_TABLE=agent_logs   # 기본 테이블명

    스키마:
        id        BIGSERIAL PRIMARY KEY
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        level     TEXT NOT NULL
        source    TEXT NOT NULL DEFAULT ''
        message   TEXT NOT NULL
        metadata  JSONB

    metadata 컬럼이 JSONB이므로 Postgres에서 직접 메타데이터 필드 검색이 가능합니다.
    예: SELECT * FROM agent_logs WHERE metadata->>'flight_id' = 'KE123';
    """

    def __init__(self, dsn: str, table: str = "agent_logs"):
        self.dsn = dsn
        self.table = table
        self._init_db()

    def _connect(self):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL 백엔드에는 psycopg2가 필요합니다: pip install psycopg2-binary"
            ) from exc
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = False
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table} (
                        id        BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        level     TEXT        NOT NULL,
                        source    TEXT        NOT NULL DEFAULT '',
                        message   TEXT        NOT NULL,
                        metadata  JSONB
                    )
                """)
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self.table}_ts  "
                    f"ON {self.table}(timestamp)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self.table}_lvl "
                    f"ON {self.table}(level)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self.table}_src "
                    f"ON {self.table}(source)"
                )
            conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_dict(row: tuple, description) -> dict:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        return {
            "id":        d.get("id"),
            "timestamp": d["timestamp"].isoformat() if hasattr(d.get("timestamp"), "isoformat") else str(d.get("timestamp", "")),
            "level":     d.get("level", ""),
            "source":    d.get("source", ""),
            "message":   d.get("message", ""),
            "metadata":  d.get("metadata") or {},
        }

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {self.table} (timestamp, level, source, message, metadata) "
                        f"VALUES (%s, %s, %s, %s, %s)",
                        (self._now_iso(), lvl, source, message,
                         json.dumps(metadata or {}, default=str)),
                    )
                conn.commit()
            return MCPResult.ok(data="logged")
        except Exception as exc:
            logger.error("postgres.logging.write 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        clauses: list[str] = []
        params: list = []

        if level:
            clauses.append("level = %s")
            params.append(level.upper())
        if source:
            clauses.append("source = %s")
            params.append(source)
        if since:
            clauses.append("timestamp >= %s")
            params.append(since)
        if until:
            clauses.append("timestamp <= %s")
            params.append(until)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        sql = (f"SELECT id, timestamp, level, source, message, metadata "
               f"FROM {self.table} {where} ORDER BY timestamp ASC LIMIT %s")

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    desc = cur.description
            return MCPResult.ok(data=[self._row_to_dict(r, desc) for r in rows])
        except Exception as exc:
            logger.error("postgres.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        n = max(1, min(n, 500))
        params: list = []
        where = ""
        if source:
            where = "WHERE source = %s"
            params.append(source)
        params.append(n)

        sql = (f"SELECT id, timestamp, level, source, message, metadata "
               f"FROM (SELECT * FROM {self.table} {where} ORDER BY id DESC LIMIT %s) sub "
               f"ORDER BY id ASC")
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    desc = cur.description
            return MCPResult.ok(data=[self._row_to_dict(r, desc) for r in rows])
        except Exception as exc:
            logger.error("postgres.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        clauses: list[str] = []
        params: list = []

        if before:
            clauses.append("timestamp < %s")
            params.append(before)
        if source:
            clauses.append("source = %s")
            params.append(source)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"DELETE FROM {self.table} {where}"

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    deleted = cur.rowcount
                conn.commit()
            return MCPResult.ok(data={"deleted": deleted})
        except Exception as exc:
            logger.error("postgres.logging.clear 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {self.table}")
                    count = cur.fetchone()[0]
            return MCPResult.ok(data={
                "backend":    "postgres",
                "table":      self.table,
                "total_logs": count,
            })
        except Exception as exc:
            return MCPResult.fail(f"PostgreSQL 연결 실패: {exc}")
