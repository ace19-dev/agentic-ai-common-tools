"""
Flight Monitor — Specialized Agent Nodes
==========================================

4 agents with distinct roles, each bound to a restricted tool set:

  SearchAgent        → Calls flight search API, stores results in memory
  PriceAnalysisAgent → Reads results, decides whether to book (structured output)
  BookingAgent       → Executes the booking API call when price is acceptable
  NotificationAgent  → Sends Slack/email alerts with outcome

Each node sets `active_phase` so the shared ToolNode knows where to route back.
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
from tools.auth_tools import auth_get_key
from tools.http_tools import http_get, http_post
from tools.memory_tools import memory_get, memory_set
from tools.notification_tools import notify_console, notify_email, notify_slack

logger = logging.getLogger(__name__)

# ── Tool sets per agent (principle of least privilege) ────────────────────────

SEARCH_TOOLS = [http_get, memory_set, notify_console]
BOOKING_TOOLS = [http_post, memory_get, memory_set, auth_get_key, notify_console]
NOTIFICATION_TOOLS = [memory_get, notify_email, notify_slack, notify_console, memory_set]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Search Agent
# ═══════════════════════════════════════════════════════════════════════════════

_SEARCH_PROMPT = """You are the Search Agent in a flight monitoring multi-agent system.
Your ONLY job for this cycle: search for available flights and store the results.

Step 1 — Call http_get with:
  url: "{api_base_url}/api/flights/search"
  params: '{{"origin": "{origin}", "destination": "{destination}", "date": "{travel_date}", "max_price": "{max_price}"}}'

Step 2 — Store the full response body in memory:
  Call memory_set(key="latest_search", namespace="flights", value=<full JSON body string>)

Step 3 — Log the outcome:
  Call notify_console(level="INFO",
    message="[Check #{check_number}] {origin}→{destination} on {travel_date} | "
            "Cheapest: $<price> USD | Threshold: ${max_price} USD")

Step 4 — End with exactly:
  SEARCH_DONE: check={check_number} cheapest=$<price>
"""


def search_node(state: FlightState) -> dict:
    """Call the flight search API and store results in memory.

    Sets active_phase="search" so the shared ToolNode routes back here
    after tool calls complete.

    Args:
        state: Current FlightState with search criteria and API base URL.

    Returns:
        Partial FlightState update: appended message + active_phase="search".
    """
    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).bind_tools(SEARCH_TOOLS)
    system = _SEARCH_PROMPT.format(
        api_base_url=state["api_base_url"],
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
        logger.error("search_node error: %s", exc)
        response = AIMessage(content=f"SEARCH_DONE: check={state['check_number']} error={exc}")

    return {"messages": [response], "active_phase": "search"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Price Analysis Agent  (structured output — no tool calls)
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
    """Decide whether to book based on search results from memory.

    Reads directly from the Memory MCP (no tool call) to avoid an extra LLM
    roundtrip.  Uses with_structured_output(_PriceDecision) so the booking
    decision is always a typed Pydantic object, not free-form text.

    Args:
        state: Current FlightState; max_price is the booking threshold.

    Returns:
        Partial FlightState update with should_book, cheapest_price,
        cheapest_flight, and active_phase="price_analysis".
    """
    from mcp.memory import get_memory_mcp
    mem = get_memory_mcp()
    result = mem.get("latest_search", namespace="flights")
    if not result.success:
        msg = AIMessage(content="ANALYSIS_DONE: no search data found — skipping")
        return {
            "messages": [msg],
            "active_phase": "price_analysis",
            "should_book": False,
        }

    raw = result.data
    # memory_set stores the search response as a JSON string; memory_get
    # may return it as str or dict depending on how the agent serialised it.
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    else:
        data = raw

    llm = ChatOpenAI(model=config.LLM_MODEL, temperature=0).with_structured_output(_PriceDecision)
    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a price analysis specialist. "
         "Examine the flight search results and decide whether the cheapest available "
         f"flight price is below the maximum acceptable price of ${state['max_price']} USD. "
         "Return a structured decision."),
        ("human", "Flight search results:\n{data}"),
    ])
    try:
        decision: _PriceDecision = (prompt | llm).invoke({"data": json.dumps(data, indent=2)})
    except Exception as exc:
        logger.error("price_analysis_node structured output error: %s", exc)
        msg = AIMessage(content="ANALYSIS_DONE: LLM error — defaulting to no-book")
        return {
            "messages": [msg],
            "active_phase": "price_analysis",
            "should_book": False,
        }

    verdict = "✅ BOOK IT" if decision.should_book else "⏭  SKIP"
    summary = (
        f"ANALYSIS_DONE [{verdict}]: "
        f"{decision.cheapest_airline} {decision.cheapest_flight_id} "
        f"@ ${decision.cheapest_price:.2f} USD "
        f"(threshold ${state['max_price']:.2f}) — {decision.analysis_notes}"
    )
    msg = AIMessage(content=summary)
    logger.info("PriceAnalysis: %s", summary)

    return {
        "messages": [msg],
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
A cheap flight has been identified. Your job is to execute the booking.

Cheapest flight details:
  Flight ID : {flight_id}
  Airline   : {airline}
  Route     : {origin} → {destination}
  Departure : {departure}
  Price     : ${price} USD
  Passenger : {passenger_name}

Step 1 — Call http_post to book the flight:
  url: "{api_base_url}/api/flights/book"
  json_body: '{{"flight_id": "{flight_id}", "airline": "{airline}", "origin": "{origin}", "destination": "{destination}", "departure": "{departure}", "price": {price}, "currency": "USD", "passenger_name": "{passenger_name}"}}'

Step 2 — Store the booking confirmation in memory:
  Call memory_set(key="booking_confirmation", namespace="flights", value=<full booking response>)

Step 3 — Log:
  Call notify_console(level="INFO",
    message="Flight booked! Ref: <booking_reference> | {airline} {flight_id} | ${price} USD")

Step 4 — End with exactly one of:
  BOOKING_CONFIRMED: ref=<booking_reference> price=<price>
  BOOKING_FAILED: <reason>
"""


def booking_node(state: FlightState) -> dict:
    """POST the booking request and store the confirmation in memory.

    Sets active_phase="booking" so the shared ToolNode routes back here
    after tool calls complete.

    Args:
        state: Current FlightState with cheapest_flight and passenger details.

    Returns:
        Partial FlightState update: appended message + active_phase="booking".
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
        api_base_url=state["api_base_url"],
    )
    messages = [SystemMessage(content=system)] + list(state["messages"])
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        logger.error("booking_node error: %s", exc)
        response = AIMessage(content=f"BOOKING_FAILED: {exc}")

    return {"messages": [response], "active_phase": "booking"}


def extract_booking_result(state: FlightState) -> dict:
    """Read the booking confirmation from memory and hydrate FlightState.

    This is an inline state-update node (not an LLM call).  It is called
    after BookingAgent's ToolNode pass completes and before NotificationAgent
    runs, so notification prompts can reference booking_reference and
    confirmed_price directly from state.

    Args:
        state: Current FlightState after BookingAgent's tool calls.

    Returns:
        Partial FlightState update: booking_confirmed, booking_reference,
        confirmed_price.
    """
    from mcp.memory import get_memory_mcp
    mem = get_memory_mcp()
    result = mem.get("booking_confirmation", namespace="flights")
    if not result.success:
        return {"booking_confirmed": False, "booking_reference": None, "confirmed_price": None}

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
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Notification Agent
# ═══════════════════════════════════════════════════════════════════════════════

_NOTIFY_BOOKED_PROMPT = """You are the Notification Agent. A flight has just been booked!
Send booking confirmation notifications.

Booking details:
  Reference  : {reference}
  Route      : {origin} → {destination}
  Airline    : {airline}
  Departure  : {departure}
  Price      : ${price} USD
  Passenger  : {passenger_name}
  Check #    : {check_number}

Step 1 — Send Slack notification:
  Call notify_slack(channel="#travel-alerts",
    message="✈️ *항공권 예약 완료!*\\n"
            "• 노선: {origin} → {destination}\\n"
            "• 항공사: {airline}\\n"
            "• 출발: {departure}\\n"
            "• 금액: *${price} USD* (임계값: ${max_price} USD)\\n"
            "• 예약번호: *{reference}*\\n"
            "• 승객: {passenger_name}\\n"
            "• 모니터링 {check_number}회차에서 발견")

Step 2 — Send email notification:
  Call notify_email(
    to="{notify_email}",
    subject="✈️ 항공권 예약 완료: {origin}→{destination} ({price} USD)",
    body="안녕하세요,\\n\\n항공권이 성공적으로 예약되었습니다.\\n\\n"
         "예약 정보:\\n"
         "- 노선: {origin} → {destination}\\n"
         "- 항공사: {airline}\\n"
         "- 출발: {departure}\\n"
         "- 금액: ${price} USD\\n"
         "- 예약번호: {reference}\\n"
         "- 승객: {passenger_name}\\n\\n"
         "총 {check_number}회 모니터링 끝에 임계값(${max_price} USD) 이하 항공권을 발견하여 자동 예약했습니다.\\n\\n감사합니다.")

Step 3 — Store notification log:
  Call memory_set(key="notification_sent", namespace="flights", value="booked:{reference}")

Step 4 — End with: NOTIFICATION_SENT: booked
"""

_NOTIFY_SKIP_PROMPT = """You are the Notification Agent. No cheap flights found this check.
Send a brief status update.

Check info:
  Check #        : {check_number}
  Cheapest found : ${cheapest_price:.2f} USD
  Threshold      : ${max_price} USD

Step 1 — Log to console:
  Call notify_console(level="INFO",
    message="[Check #{check_number}] No deal found. "
            "Cheapest: ${cheapest_price:.2f} USD (need < ${max_price:.2f} USD). "
            "Monitoring continues...")

Step 2 — End with: NOTIFICATION_SENT: skipped
"""


def notification_node(state: FlightState) -> dict:
    """Send booking confirmation or 'no deal' status via Slack/email/console.

    Uses two different system prompts depending on whether a booking was made:
      - booking_confirmed=True  → full Slack + email notification with reference
      - booking_confirmed=False → brief console log only (no external calls)

    Sets active_phase="notification" so the shared ToolNode routes back here.

    Args:
        state: Current FlightState with booking result and cheapest_flight.

    Returns:
        Partial FlightState update: appended message + active_phase="notification".
    """
    booking_confirmed = state.get("booking_confirmed", False)
    flight = state.get("cheapest_flight") or {}

    if booking_confirmed:
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
            notify_email=_get_notify_email(),
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
        logger.error("notification_node error: %s", exc)
        response = AIMessage(content=f"NOTIFICATION_SENT: error={exc}")

    return {"messages": [response], "active_phase": "notification"}


def _get_notify_email() -> str:
    """Return the notification recipient email from env, with a safe placeholder fallback."""
    import os
    return os.getenv("EMAIL_RECIPIENT", "traveler@example.com")
