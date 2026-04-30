"""
FlightState — 항공편 모니터 멀티에이전트 그래프의 공유 상태.

모니터링 사이클마다 run.py가 새로운 FlightState를 생성합니다.
필드는 에이전트 파이프라인을 반영하는 네 가지 논리 섹션으로 구성됩니다:
  1. 검색 조건   — MonitorCriteria가 제공하는 불변 입력값
  2. 사이클 상태 — 체크 번호 및 ToolNode 라우팅용 active_phase
  3. 검색 결과   — SearchAgent + PriceAnalysisAgent가 채웁니다
  4. 예약 결과   — BookingAgent + extract_booking_result가 채웁니다
"""
from __future__ import annotations

import operator
from typing import Annotated, Dict, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class FlightState(TypedDict):
    """항공편 모니터 멀티에이전트 그래프를 흐르는 공유 상태.

    모니터링 체크 사이클마다 인스턴스 하나가 생성됩니다.
    run.py의 모니터링 루프는 사이클마다 새로운 state를 생성하고 check_number를 증가시킵니다.
    """

    messages: Annotated[List[BaseMessage], operator.add]

    # ── 검색 조건 (사이클 간 불변) ────────────────────────────────────────────
    origin: str           # IATA 코드, 예: "ICN"
    destination: str      # IATA 코드, 예: "NRT"
    travel_date: str      # ISO 날짜, 예: "2026-06-15"
    max_price: float      # USD 가격 임계값 — 이 가격보다 저렴하면 예약
    currency: str         # 표시 통화 ("USD")
    passenger_name: str   # 예약자 이름
    passenger_email: str  # 예약 확인용 연락 이메일
    api_base_url: str     # 항공편 검색/예약 API 기본 URL (mock 모드 전용)

    # ── 사이클별 상태 ─────────────────────────────────────────────────────────
    check_number: int                  # 현재 모니터링 반복 횟수 (1부터 시작)
    active_phase: str                  # "search" | "price_analysis" | "booking" | "notification"

    # ── SearchAgent가 채우는 필드 ─────────────────────────────────────────────
    available_flights: List[Dict]      # 검색 API가 반환한 전체 항공편 목록
    cheapest_price: Optional[float]    # 가장 저렴한 항공편 가격
    cheapest_flight: Optional[Dict]    # 가장 저렴한 항공편의 전체 객체

    # ── PriceAnalysisAgent가 채우는 필드 ─────────────────────────────────────
    should_book: bool                  # cheapest_price < max_price이면 True

    # ── BookingAgent가 채우는 필드 ───────────────────────────────────────────
    booking_confirmed: bool            # 예약 API가 CONFIRMED를 반환하면 True
    booking_reference: Optional[str]   # 예약 확인 코드, 예: "AGNT48271"
    confirmed_price: Optional[float]   # 최종 예약 가격
    booking_url: Optional[str]         # Google Flights 딥 링크 (live/Amadeus 모드 전용)
