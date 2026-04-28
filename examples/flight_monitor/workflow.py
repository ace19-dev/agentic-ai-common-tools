"""
Flight Monitor — LangGraph Workflow
=====================================

Graph topology (one cycle = one monitoring check):

    START
      │
      ▼
  search ──[tool_calls?]──► tools ──► search
      │
      ▼ (done)
  price_analysis                     (structured output — no tool calls)
      │
      ├─[should_book=True]──► booking ──[tool_calls?]──► tools ──► booking
      │                           │
      │                           ▼ (done)
      │                   extract_booking_result (inline state update)
      │                           │
      └─[should_book=False]──► notification ──[tool_calls?]──► tools ──► notification
                                  │
                                  ▼ (done)
                                 END

The `active_phase` field in FlightState tells the shared ToolNode which agent
to return execution to after tool calls complete.
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

# Deduplicate by tool name: memory_get and memory_set appear in multiple agent
# tool sets, so a dict keyed on name keeps only one copy of each.
_ALL_TOOLS = list({t.name: t for t in SEARCH_TOOLS + BOOKING_TOOLS + NOTIFICATION_TOOLS}.values())


# ── Routing functions ─────────────────────────────────────────────────────────

def _has_tool_calls(state: FlightState) -> bool:
    """Return True when the last message is an AIMessage with pending tool calls."""
    last = state["messages"][-1]
    return bool(getattr(last, "tool_calls", None))


def _route_search(state: FlightState) -> str:
    """Loop search→tools→search until the LLM emits no more tool calls."""
    return "tools" if _has_tool_calls(state) else "price_analysis"


def _route_price_analysis(state: FlightState) -> str:
    """Branch on the booking decision — no tool calls expected from this node."""
    return "booking" if state.get("should_book") else "notification"


def _route_booking(state: FlightState) -> str:
    """Loop booking→tools→booking until the booking API call is complete."""
    return "tools" if _has_tool_calls(state) else "extract_booking"


def _route_notification(state: FlightState) -> str:
    """Loop notification→tools→notification, then exit to END."""
    return "tools" if _has_tool_calls(state) else END


def _route_after_tools(state: FlightState) -> str:
    """Route back to whichever agent triggered the tool calls.

    active_phase is set by each agent node before returning so the single
    shared ToolNode always knows where to send execution after tools run.
    """
    return state.get("active_phase", "search")


# ── extract_booking_result wrapper ────────────────────────────────────────────

def _extract_booking_node(state: FlightState) -> dict:
    """Thin wrapper so extract_booking_result can be registered as a graph node."""
    updates = extract_booking_result(state)
    logger.info(
        "Booking result: confirmed=%s ref=%s price=%s",
        updates.get("booking_confirmed"),
        updates.get("booking_reference"),
        updates.get("confirmed_price"),
    )
    return updates


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_flight_graph():
    """Build and compile the flight monitoring multi-agent graph.

    Returns a compiled LangGraph ready for `.invoke()` per monitoring cycle.
    """
    tool_node = ToolNode(_ALL_TOOLS)

    graph = StateGraph(FlightState)

    # Nodes
    graph.add_node("search", search_node)
    graph.add_node("price_analysis", price_analysis_node)
    graph.add_node("booking", booking_node)
    graph.add_node("extract_booking", _extract_booking_node)
    graph.add_node("notification", notification_node)
    graph.add_node("tools", tool_node)

    # Entry
    graph.set_entry_point("search")

    # Edges
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

    # After booking result is extracted, always proceed to notification
    graph.add_edge("extract_booking", "notification")

    graph.add_conditional_edges(
        "notification",
        _route_notification,
        {"tools": "tools", END: END},
    )

    # Tools always route back to whichever agent triggered them
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
