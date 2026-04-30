from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

from core.base_mcp import MCPResult
from mcp.backends.logging.base import BaseLoggingBackend

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class FileLoggingBackend(BaseLoggingBackend):
    """구조화 로그를 회전(rotating) JSON Lines 파일에 기록하는 백엔드.

    각 줄은 JSON 객체 하나이므로 tail / grep 등 표준 도구로 바로 분석할 수 있습니다.
    max_bytes 도달 시 backup_count 개의 .1 .2 ... 파일로 자동 회전됩니다.
    query/tail은 현재 파일만 검색합니다(회전된 파일은 제외).
    """

    def __init__(self,
                 log_path: str = "data/agent.log",
                 max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 5):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)

        # Python logging 핸들러는 실제 파일 회전만 담당
        self._handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        self._handler.setLevel(logging.DEBUG)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_line(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        self._handler.stream.write(line)
        self._handler.stream.flush()
        # 회전 필요 여부 체크
        if self._handler.shouldRollover(None):  # type: ignore[arg-type]
            self._handler.doRollover()

    def _read_lines(self) -> list[dict]:
        """현재 로그 파일의 모든 유효한 JSON 줄을 파싱해 반환합니다."""
        entries: list[dict] = []
        if not os.path.exists(self.log_path):
            return entries
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        return entries

    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        lvl = level.upper()
        if lvl not in _VALID_LEVELS:
            lvl = "INFO"
        entry = {
            "id":        None,
            "timestamp": self._now_iso(),
            "level":     lvl,
            "source":    source,
            "message":   message,
            "metadata":  metadata or {},
        }
        try:
            self._write_line(entry)
            return MCPResult.ok(data="logged")
        except Exception as exc:
            logger.error("file.logging.write 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        try:
            entries = self._read_lines()
            if level:
                entries = [e for e in entries if e.get("level") == level.upper()]
            if source:
                entries = [e for e in entries if e.get("source") == source]
            if since:
                entries = [e for e in entries if e.get("timestamp", "") >= since]
            if until:
                entries = [e for e in entries if e.get("timestamp", "") <= until]
            return MCPResult.ok(data=entries[-limit:])
        except Exception as exc:
            logger.error("file.logging.query 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        try:
            entries = self._read_lines()
            if source:
                entries = [e for e in entries if e.get("source") == source]
            return MCPResult.ok(data=entries[-max(1, min(n, 500)):])
        except Exception as exc:
            logger.error("file.logging.tail 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        try:
            if before is None and source is None:
                # 전체 삭제: 파일 초기화
                open(self.log_path, "w").close()
                return MCPResult.ok(data={"deleted": "all"})

            entries = self._read_lines()
            original = len(entries)
            if before:
                entries = [e for e in entries if e.get("timestamp", "") >= before]
            if source:
                entries = [e for e in entries if e.get("source") != source]

            with open(self.log_path, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

            return MCPResult.ok(data={"deleted": original - len(entries)})
        except Exception as exc:
            logger.error("file.logging.clear 실패: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        try:
            size = os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0
            return MCPResult.ok(data={
                "backend":     "file",
                "path":        self.log_path,
                "size_bytes":  size,
            })
        except Exception as exc:
            return MCPResult.fail(str(exc))
