from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from core.base_mcp import MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class SQLiteLoggingBackend(BaseLoggingBackend):
    """구조화 로그를 로컬 SQLite 파일에 저장하는 백엔드.

    스키마: logs(id, timestamp, level, source, message, metadata)
    timestamp은 ISO 8601 문자열(UTC)로 저장되어 텍스트 정렬로 시간순 조회가 가능합니다.
    """

    def __init__(self, db_path: str = "data/agent_logs.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT    NOT NULL,
                    level     TEXT    NOT NULL,
                    source    TEXT    NOT NULL DEFAULT '',
                    message   TEXT    NOT NULL,
                    metadata  TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_ts  ON logs(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_lvl ON logs(level)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_src ON logs(source)")
            conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        meta = row["metadata"]
        return {
            "id":        row["id"],
            "timestamp": row["timestamp"],
            "level":     row["level"],
            "source":    row["source"],
            "message":   row["message"],
            "metadata":  json.loads(meta) if meta else {},
        }

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO logs (timestamp, level, source, message, metadata) VALUES (?,?,?,?,?)",
                    (self._now_iso(), lvl, source,
                     message, json.dumps(metadata or {}, default=str)),
                )
                conn.commit()
            return MCPResult.ok(data="logged")
        except Exception as exc:
            logger.error("sqlite.logging.write 실패: %s", exc)
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
            clauses.append("level = ?")
            params.append(level.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM logs {where} ORDER BY timestamp ASC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return MCPResult.ok(data=[self._row_to_dict(r) for r in rows])
        except Exception as exc:
            logger.error("sqlite.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        n = max(1, min(n, 500))
        try:
            with self._connect() as conn:
                if source:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM logs WHERE source=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                        (source, n),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM logs ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                        (n,),
                    ).fetchall()
            return MCPResult.ok(data=[self._row_to_dict(r) for r in rows])
        except Exception as exc:
            logger.error("sqlite.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        clauses: list[str] = []
        params: list = []

        if before:
            clauses.append("timestamp < ?")
            params.append(before)
        if source:
            clauses.append("source = ?")
            params.append(source)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"DELETE FROM logs {where}"

        try:
            with self._connect() as conn:
                cursor = conn.execute(sql, params)
                conn.commit()
            return MCPResult.ok(data={"deleted": cursor.rowcount})
        except Exception as exc:
            logger.error("sqlite.logging.clear 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            return MCPResult.ok(data={"backend": "sqlite", "db": self.db_path, "total_logs": count})
        except Exception as exc:
            return MCPResult.fail(str(exc))
