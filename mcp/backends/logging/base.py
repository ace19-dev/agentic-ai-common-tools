from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.base_mcp import MCPResult


class BaseLoggingBackend(ABC):
    """로깅 백엔드 추상 기반 클래스.

    모든 구현체는 write/query/tail/clear/health_check 를 제공해야 합니다.
    각 로그 엔트리는 다음 딕셔너리 형태로 반환됩니다:
        {
            "id":        int | None,   # SQLite는 행 ID, 나머지는 None
            "timestamp": str,          # ISO 8601 (마이크로초 포함)
            "level":     str,          # DEBUG | INFO | WARNING | ERROR | CRITICAL
            "source":    str,          # 에이전트/모듈 이름 (빈 문자열 가능)
            "message":   str,
            "metadata":  dict,         # 추가 구조화 데이터
        }
    """

    @abstractmethod
    def write(self, level: str, message: str,
              source: str = "", metadata: Optional[dict] = None) -> MCPResult:
        """로그 엔트리 1건을 기록합니다."""
        ...

    @abstractmethod
    def query(self,
              level: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              limit: int = 100) -> MCPResult:
        """조건에 맞는 로그 엔트리 목록을 반환합니다 (오래된 것부터 정렬)."""
        ...

    @abstractmethod
    def tail(self, n: int = 20, source: Optional[str] = None) -> MCPResult:
        """가장 최근 n개의 로그 엔트리를 반환합니다."""
        ...

    @abstractmethod
    def clear(self, before: Optional[str] = None,
              source: Optional[str] = None) -> MCPResult:
        """로그 엔트리를 삭제합니다. before(ISO 문자열) 이전 것만 삭제하거나 전체 삭제."""
        ...

    @abstractmethod
    def health_check(self) -> MCPResult:
        ...
