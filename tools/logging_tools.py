"""
Logging tools — 에이전트가 사용하는 LangChain @tool 래퍼.

LoggingMCP 위에 얇게 올라가는 4개의 tool:
  log_write  — 구조화 로그 엔트리 기록
  log_query  — 조건(레벨/소스/기간)으로 로그 검색
  log_tail   — 최신 N개 엔트리 조회
  log_clear  — 오래된 로그 삭제
"""
import json

from langchain_core.tools import tool

from mcp.logging_mcp import get_logging_mcp


@tool
def log_write(level: str, message: str,
              source: str = "", metadata: str = "{}") -> str:
    """로그 엔트리 1건을 기록합니다.

    Args:
        level:    로그 레벨 — DEBUG | INFO | WARNING | ERROR | CRITICAL
        message:  로그 메시지 (자유 형식 텍스트)
        source:   발신 에이전트 또는 모듈 이름 (예: "search_agent", "booking_agent")
        metadata: 추가 구조화 데이터를 JSON 문자열로 전달 (예: '{"flight_id": "KE123"}')

    Returns:
        "logged" 또는 오류 메시지
    """
    try:
        meta = json.loads(metadata) if metadata and metadata != "{}" else {}
    except json.JSONDecodeError:
        meta = {"raw": metadata}
    return get_logging_mcp().write(level, message, source=source, metadata=meta).to_tool_str()


@tool
def log_query(level: str = "", source: str = "",
              since: str = "", until: str = "",
              limit: int = 50) -> str:
    """조건에 맞는 로그 엔트리를 검색합니다.

    Args:
        level:  필터할 로그 레벨 (빈 문자열이면 전체 레벨 조회)
        source: 필터할 소스 이름 (빈 문자열이면 전체 소스 조회)
        since:  이 시각 이후 항목만 반환 (ISO 8601, 예: "2026-04-30T00:00:00")
        until:  이 시각 이전 항목만 반환 (ISO 8601)
        limit:  최대 반환 건수 (기본 50, 최대 1000)

    Returns:
        로그 엔트리 목록의 JSON 문자열
    """
    return get_logging_mcp().query(
        level=level or None,
        source=source or None,
        since=since or None,
        until=until or None,
        limit=limit,
    ).to_tool_str()


@tool
def log_tail(n: int = 20, source: str = "") -> str:
    """가장 최근 N개의 로그 엔트리를 조회합니다.

    Args:
        n:      반환할 엔트리 수 (기본 20, 최대 500)
        source: 특정 소스로 필터링 (빈 문자열이면 전체 소스)

    Returns:
        최신 로그 엔트리 목록의 JSON 문자열 (오래된 것부터 정렬)
    """
    return get_logging_mcp().tail(n=n, source=source or None).to_tool_str()


@tool
def log_clear(before: str = "", source: str = "") -> str:
    """로그 엔트리를 삭제합니다.

    Args:
        before: 이 시각(ISO 8601) 이전 항목만 삭제. 빈 문자열이면 전체 삭제.
        source: 특정 소스의 로그만 삭제. 빈 문자열이면 소스 필터 없음.

    Returns:
        삭제된 건수 또는 오류 메시지
    """
    return get_logging_mcp().clear(
        before=before or None,
        source=source or None,
    ).to_tool_str()
