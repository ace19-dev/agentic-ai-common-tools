"""
Flight Monitor — 전문 에이전트 노드
======================================

각 에이전트는 제한된 tool 세트에 바인딩되어 고유한 역할을 담당합니다:

  SearchAgent        → flight_search 호출, 결과를 메모리에 저장
  PriceAnalysisAgent → 결과 읽기, 예약 여부 결정 (구조화 출력)
  BookingAgent       → 가격이 적절하면 flight_book 호출
  NotificationAgent  → Slack/이메일 알림 발송

mock 모드: flight_search/flight_book이 로컬 MockFlightAPI 서버를 호출합니다.
live 모드: flight_search가 Amadeus를 호출하고, flight_book은 딜 참조번호와
           Google Flights 딥 링크를 반환해 사용자가 직접 구매를 완료하도록 합니다.

각 노드는 공유 ToolNode가 올바른 에이전트로 돌아올 수 있도록 `active_phase`를 설정합니다.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

import config
from examples.flight_monitor.state import FlightState
from tools.flight_tools import flight_book, flight_search
from tools.memory_tools import memory_get, memory_set
from tools.notification_tools import notify_console, notify_email, notify_slack

logger = logging.getLogger(__name__)

# ── 에이전트별 tool 세트 (최소 권한 원칙) ─────────────────────────────────────

SEARCH_TOOLS = [flight_search, memory_set, notify_console]
BOOKING_TOOLS = [flight_book, memory_get, memory_set, notify_console]
NOTIFICATION_TOOLS = [memory_get, notify_email, notify_slack, notify_console, memory_set]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Search Agent
# ═══════════════════════════════════════════════════════════════════════════════

_SEARCH_PROMPT = """You are the Search Agent in a flight monitoring multi-agent system.
Your ONLY job for this cycle: search for available flights and store the results.

Step 1 — Call flight_search with these exact arguments:
  origin="{origin}"
  destination="{destination}"
  date="{travel_date}"
  max_price={max_price}
  check_number={check_number}

Step 2 — Store the full JSON response in memory:
  Call memory_set(key="latest_search", namespace="flights", value=<full JSON string from flight_search>)

Step 3 — Log the result:
  Call notify_console(level="INFO",
    message="Check #{check_number}: {origin}→{destination} | Cheapest: $<price> USD | Threshold: ${max_price} USD")

Step 4 — End your response with exactly:
  SEARCH_DONE: check={check_number} cheapest=$<price>
"""


def search_node(state: FlightState) -> dict:
    """항공편 검색 tool을 호출하고 결과를 메모리에 저장합니다.

    tool 호출 완료 후 공유 ToolNode가 이 노드로 돌아오도록 active_phase="search"를 설정합니다.
    """
    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).bind_tools(SEARCH_TOOLS)
    system = _SEARCH_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        travel_date=state["travel_date"],
        max_price=state["max_price"],
        check_number=state["check_number"],
    )
    messages = [SystemMessage(content=system)] + list(state["messages"])
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        logger.error("search_node 오류: %s", exc)
        response = AIMessage(content=f"SEARCH_DONE: check={state['check_number']} error={exc}")

    return {"messages": [response], "active_phase": "search"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Price Analysis Agent  (구조화 출력 — tool 호출 없음)
# ═══════════════════════════════════════════════════════════════════════════════


class _PriceDecision(BaseModel):
    should_book: bool = Field(
        description="True if the cheapest flight price is strictly below max_price"
    )
    cheapest_price: float = Field(description="Lowest price found in USD")
    cheapest_airline: str = Field(description="Airline name of the cheapest flight")
    cheapest_flight_id: str = Field(description="flight_id of the cheapest option")
    departure: str = Field(description="Departure datetime string of the cheapest flight")
    seats_available: int = Field(description="Seats available on the cheapest flight")
    analysis_notes: str = Field(description="One-sentence explanation of the decision")


def price_analysis_node(state: FlightState) -> dict:
    """메모리에 저장된 검색 결과를 바탕으로 예약 여부를 결정합니다.

    추가 LLM 왕복을 피하기 위해 Memory MCP에서 직접 읽습니다.
    with_structured_output을 사용하므로 결정 결과는 항상 타입이 지정된
    Pydantic 객체로 반환되며 자유 형식 텍스트가 아닙니다.
    """
    from mcp.memory import get_memory_mcp
    mem = get_memory_mcp()
    result = mem.get("latest_search", namespace="flights")
    if not result.success:
        msg = AIMessage(content="ANALYSIS_DONE: no search data found — skipping")
        return {"messages": [msg], "active_phase": "price_analysis", "should_book": False}

    raw = result.data
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    else:
        data = raw or {}

    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).with_structured_output(_PriceDecision)
    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a price analysis specialist. "
         "Examine the flight search results and decide whether the cheapest available "
         f"flight is below the maximum acceptable price of ${state['max_price']} USD. "
         "Return a structured decision."),
        ("human", "Flight search results:\n{data}"),
    ])
    try:
        decision: _PriceDecision = (prompt | llm).invoke({"data": json.dumps(data, indent=2)})
    except Exception as exc:
        logger.error("price_analysis_node 오류: %s", exc)
        msg = AIMessage(content="ANALYSIS_DONE: LLM error — defaulting to no-book")
        return {"messages": [msg], "active_phase": "price_analysis", "should_book": False}

    verdict = "BOOK IT" if decision.should_book else "SKIP"
    summary = (
        f"ANALYSIS_DONE [{verdict}]: "
        f"{decision.cheapest_airline} {decision.cheapest_flight_id} "
        f"@ ${decision.cheapest_price:.2f} USD "
        f"(threshold ${state['max_price']:.2f}) — {decision.analysis_notes}"
    )
    logger.info("PriceAnalysis: %s", summary)

    return {
        "messages": [AIMessage(content=summary)],
        "active_phase": "price_analysis",
        "should_book": decision.should_book,
        "cheapest_price": decision.cheapest_price,
        "cheapest_flight": {
            "flight_id": decision.cheapest_flight_id,
            "airline": decision.cheapest_airline,
            "departure": decision.departure,
            "price": decision.cheapest_price,
            "seats_available": decision.seats_available,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Booking Agent
# ═══════════════════════════════════════════════════════════════════════════════

_BOOKING_PROMPT = """You are the Booking Agent in a flight monitoring multi-agent system.
A deal has been found. Your job is to book the flight.

Cheapest flight details:
  Flight ID  : {flight_id}
  Airline    : {airline}
  Route      : {origin} → {destination}
  Departure  : {departure}
  Price      : ${price} USD
  Passenger  : {passenger_name}
  Email      : {passenger_email}

Step 1 — Call flight_book with these exact arguments:
  flight_id="{flight_id}"
  airline="{airline}"
  origin="{origin}"
  destination="{destination}"
  departure="{departure}"
  price={price}
  passenger_name="{passenger_name}"
  passenger_email="{passenger_email}"

Step 2 — Store the booking result in memory:
  Call memory_set(key="booking_confirmation", namespace="flights", value=<full JSON from flight_book>)

Step 3 — Log the outcome:
  Call notify_console(level="INFO",
    message="Booking result: <status> | Ref: <booking_reference> | {airline} {flight_id} | ${price} USD")

Step 4 — End your response with:
  BOOKING_CONFIRMED: ref=<booking_reference> price=<price>
  or BOOKING_FAILED: <reason>
"""


def booking_node(state: FlightState) -> dict:
    """flight_book tool을 통해 가장 저렴한 항공편을 예약합니다.

    tool 호출 완료 후 공유 ToolNode가 이 노드로 돌아오도록 active_phase="booking"을 설정합니다.
    """
    flight = state.get("cheapest_flight") or {}
    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).bind_tools(BOOKING_TOOLS)
    system = _BOOKING_PROMPT.format(
        flight_id=flight.get("flight_id", ""),
        airline=flight.get("airline", ""),
        origin=state["origin"],
        destination=state["destination"],
        departure=flight.get("departure", ""),
        price=flight.get("price", 0),
        passenger_name=state["passenger_name"],
        passenger_email=state.get("passenger_email", ""),
    )
    messages = [SystemMessage(content=system)] + list(state["messages"])
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        logger.error("booking_node 오류: %s", exc)
        response = AIMessage(content=f"BOOKING_FAILED: {exc}")

    return {"messages": [response], "active_phase": "booking"}


def extract_booking_result(state: FlightState) -> dict:
    """메모리에서 예약 확인 정보를 읽어 FlightState를 업데이트합니다.

    BookingAgent의 ToolNode 패스 완료 후, NotificationAgent 실행 전에
    인라인 노드로 호출됩니다. 덕분에 알림 프롬프트에서 state의
    booking_reference, confirmed_price, booking_url를 직접 참조할 수 있습니다.
    """
    from mcp.memory import get_memory_mcp
    mem = get_memory_mcp()
    result = mem.get("booking_confirmation", namespace="flights")
    if not result.success:
        return {
            "booking_confirmed": False,
            "booking_reference": None,
            "confirmed_price": None,
            "booking_url": None,
        }

    raw = result.data
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    else:
        data = raw if isinstance(raw, dict) else {}

    confirmed = data.get("status", "").upper() == "CONFIRMED"
    return {
        "booking_confirmed": confirmed,
        "booking_reference": data.get("booking_reference"),
        "confirmed_price": data.get("price"),
        "booking_url": data.get("booking_url"),  # live/Amadeus 모드에서만 존재
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Notification Agent
# ═══════════════════════════════════════════════════════════════════════════════

_NOTIFY_BOOKED_PROMPT = """You are the Notification Agent. A flight has been booked (or deal confirmed)!
Send booking confirmation notifications.

Booking details:
  Reference  : {reference}
  Route      : {origin} → {destination}
  Airline    : {airline}
  Departure  : {departure}
  Price      : ${price} USD (threshold: ${max_price} USD)
  Passenger  : {passenger_name}
  Check #    : {check_number}
  Booking URL: {booking_url}

Step 1 — Send Slack notification:
  Call notify_slack(channel="#travel-alerts",
    message="✈️ *항공권 딜 발견 및 예약 완료!*\\n"
            "• 노선: {origin} → {destination}\\n"
            "• 항공사: {airline}\\n"
            "• 출발: {departure}\\n"
            "• 금액: *${price} USD* (임계값: ${max_price} USD)\\n"
            "• 예약번호: *{reference}*\\n"
            "• 승객: {passenger_name}\\n"
            "{booking_url_line}"
            "• 모니터링 {check_number}회차에서 발견")

Step 2 — Send email notification:
  Call notify_email(
    to="{notify_email}",
    subject="✈️ 항공권 예약 완료: {origin}→{destination} (${price} USD)",
    body="안녕하세요,\\n\\n항공권이 예약되었습니다.\\n\\n"
         "예약 정보:\\n"
         "- 노선: {origin} → {destination}\\n"
         "- 항공사: {airline}\\n"
         "- 출발: {departure}\\n"
         "- 금액: ${price} USD\\n"
         "- 예약번호: {reference}\\n"
         "- 승객: {passenger_name}\\n"
         "{booking_url_body}"
         "\\n총 {check_number}회 모니터링 끝에 임계값(${max_price} USD) 이하 항공권을 발견했습니다.\\n\\n감사합니다.")

Step 3 — Store notification log:
  Call memory_set(key="notification_sent", namespace="flights", value="booked:{reference}")

Step 4 — End with: NOTIFICATION_SENT: booked
"""

_NOTIFY_SKIP_PROMPT = """You are the Notification Agent. No cheap flights found this check.

Check info:
  Check #        : {check_number}
  Cheapest found : ${cheapest_price:.2f} USD
  Threshold      : ${max_price} USD

Step 1 — Log to console:
  Call notify_console(level="INFO",
    message="Check #{check_number}: no deal. "
            "Cheapest ${cheapest_price:.2f} USD (need < ${max_price:.2f} USD). "
            "Monitoring continues...")

Step 2 — End with: NOTIFICATION_SENT: skipped
"""


def notification_node(state: FlightState) -> dict:
    """예약 확인 또는 '딜 없음' 상태를 Slack/이메일/콘솔로 발송합니다.

    booking_confirmed=True  → Slack + 이메일 전체 알림
    booking_confirmed=False → 콘솔 로그만 간략히 출력
    """
    booking_confirmed = state.get("booking_confirmed", False)
    flight = state.get("cheapest_flight") or {}
    booking_url = state.get("booking_url") or ""

    if booking_confirmed:
        booking_url_line = f"• 예약 링크: {booking_url}\\n" if booking_url else ""
        booking_url_body = f"- 예약 링크: {booking_url}\\n" if booking_url else ""
        llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).bind_tools(NOTIFICATION_TOOLS)
        system = _NOTIFY_BOOKED_PROMPT.format(
            reference=state.get("booking_reference", "N/A"),
            origin=state["origin"],
            destination=state["destination"],
            airline=flight.get("airline", ""),
            departure=flight.get("departure", ""),
            price=state.get("confirmed_price") or flight.get("price", 0),
            max_price=state["max_price"],
            passenger_name=state["passenger_name"],
            check_number=state["check_number"],
            booking_url=booking_url or "N/A",
            booking_url_line=booking_url_line,
            booking_url_body=booking_url_body,
            notify_email=state.get("passenger_email") or _default_notify_email(),
        )
    else:
        llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).bind_tools(NOTIFICATION_TOOLS)
        system = _NOTIFY_SKIP_PROMPT.format(
            check_number=state["check_number"],
            cheapest_price=state.get("cheapest_price") or 0.0,
            max_price=state["max_price"],
        )

    messages = [SystemMessage(content=system)] + list(state["messages"])
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        logger.error("notification_node 오류: %s", exc)
        response = AIMessage(content=f"NOTIFICATION_SENT: error={exc}")

    return {"messages": [response], "active_phase": "notification"}


def _default_notify_email() -> str:
    import os
    return os.getenv("EMAIL_RECIPIENT", "traveler@example.com")
