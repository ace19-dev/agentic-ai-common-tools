"""
Flight Monitor — LangGraph 워크플로우
=====================================

에이전트 실행 흐름 (그래프 1회 실행 = 모니터링 체크 1회):

    START
      │
      ▼
  search ──LLM이 tool 호출──► tools ──► search
      │
      ▼ (LLM이 tool 호출 안 함, 완료)
  price_analysis                     (구조화 출력 — tool 호출 없음)
      │
      ├─[should_book=True]──► booking ──LLM이 tool 호출──► tools ──► booking
      │                           │
      │                           ▼ (LLM이 tool 호출 안 함, 완료)
      │                   extract_booking_result (인라인 state 업데이트)
      │                           │
      └─[should_book=False]──► notification ──LLM이 tool 호출──► tools ──► notification
                                  │
                                  ▼ (LLM이 tool 호출 안 함, 완료)
                                 END

FlightState의 `active_phase` 필드가 공유 ToolNode에게
tool 호출 완료 후 어느 에이전트로 돌아갈지 알려줍니다.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from examples.flight_monitor.agents import (
    BOOKING_TOOLS,
    NOTIFICATION_TOOLS,
    SEARCH_TOOLS,
    booking_node,
    extract_booking_result,
    notification_node,
    price_analysis_node,
    search_node,
)
from examples.flight_monitor.state import FlightState

logger = logging.getLogger(__name__)

# tool 이름 기준으로 중복 제거: memory_get, memory_set이 여러 에이전트 tool 세트에 등장하므로
# 이름을 키로 하는 dict로 각 tool의 복사본을 하나만 유지합니다.
_ALL_TOOLS = list({t.name: t for t in SEARCH_TOOLS + BOOKING_TOOLS + NOTIFICATION_TOOLS}.values())


# ── 라우팅 함수 ────────────────────────────────────────────────────────────────

def _has_tool_calls(state: FlightState) -> bool:
    """마지막 메시지가 미완료 tool 호출을 포함한 AIMessage이면 True를 반환합니다."""
    last = state["messages"][-1]
    return bool(getattr(last, "tool_calls", None))


def _route_search(state: FlightState) -> str:
    """LLM이 더 이상 tool을 호출하지 않을 때까지 search→tools→search 루프를 반복합니다."""
    return "tools" if _has_tool_calls(state) else "price_analysis"


def _route_price_analysis(state: FlightState) -> str:
    """예약 결정에 따라 분기합니다 — 이 노드에서는 tool 호출이 없습니다."""
    return "booking" if state.get("should_book") else "notification"


def _route_booking(state: FlightState) -> str:
    """예약 API 호출이 완료될 때까지 booking→tools→booking 루프를 반복합니다."""
    return "tools" if _has_tool_calls(state) else "extract_booking"


def _route_notification(state: FlightState) -> str:
    """notification→tools→notification 루프를 반복한 후 END로 종료합니다."""
    return "tools" if _has_tool_calls(state) else END


def _route_after_tools(state: FlightState) -> str:
    """tool 호출을 트리거한 에이전트로 다시 라우팅합니다.

    각 에이전트 노드가 반환 전에 active_phase를 자신의 이름으로 설정해두면,
    공유 ToolNode는 tool 실행 후 항상 올바른 에이전트로 돌아갈 수 있습니다.
    """
    return state.get("active_phase", "search")


# ── extract_booking_result 래퍼 ───────────────────────────────────────────────

def _extract_booking_node(state: FlightState) -> dict:
    """extract_booking_result를 그래프 노드로 등록하기 위한 얇은 래퍼입니다."""
    updates = extract_booking_result(state)
    logger.info(
        "예약 결과: confirmed=%s ref=%s price=%s",
        updates.get("booking_confirmed"),
        updates.get("booking_reference"),
        updates.get("confirmed_price"),
    )
    return updates


# ── 그래프 빌더 ────────────────────────────────────────────────────────────────

def build_flight_graph():
    """항공편 모니터링 멀티에이전트 그래프를 빌드하고 컴파일합니다.

    모니터링 사이클마다 `.invoke()`로 호출 가능한 컴파일된 LangGraph를 반환합니다.
    """
    tool_node = ToolNode(_ALL_TOOLS)

    graph = StateGraph(FlightState)

    # 노드 등록
    graph.add_node("search", search_node)
    graph.add_node("price_analysis", price_analysis_node)
    graph.add_node("booking", booking_node)
    graph.add_node("extract_booking", _extract_booking_node)
    graph.add_node("notification", notification_node)
    graph.add_node("tools", tool_node)

    # 진입점
    graph.set_entry_point("search")

    # 엣지 연결
    graph.add_conditional_edges(
        "search",
        _route_search,
        {"tools": "tools", "price_analysis": "price_analysis"},
    )

    graph.add_conditional_edges(
        "price_analysis",
        _route_price_analysis,
        {"booking": "booking", "notification": "notification"},
    )

    graph.add_conditional_edges(
        "booking",
        _route_booking,
        {"tools": "tools", "extract_booking": "extract_booking"},
    )

    # 예약 결과 추출 후 항상 notification으로 진행
    graph.add_edge("extract_booking", "notification")

    graph.add_conditional_edges(
        "notification",
        _route_notification,
        {"tools": "tools", END: END},
    )

    # tools는 항상 자신을 트리거한 에이전트로 라우팅
    graph.add_conditional_edges(
        "tools",
        _route_after_tools,
        {
            "search": "search",
            "booking": "booking",
            "notification": "notification",
        },
    )

    return graph.compile()
