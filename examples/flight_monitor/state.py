"""
FlightState — shared state for the flight-monitor multi-agent graph.

Each monitoring cycle starts with a fresh FlightState created by run.py.
Fields are grouped into four logical sections that mirror the agent pipeline:
  1. Search criteria  — immutable inputs provided by MonitorCriteria
  2. Per-cycle state  — check number and active_phase for ToolNode routing
  3. Search results   — populated by SearchAgent + PriceAnalysisAgent
  4. Booking result   — populated by BookingAgent + extract_booking_result
"""
from __future__ import annotations

import operator
from typing import Annotated, Dict, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class FlightState(TypedDict):
    """Shared state flowing through the flight-monitor multi-agent graph.

    One instance per monitoring check cycle. The monitoring loop in run.py
    creates a fresh state for each cycle and increments check_number.
    """

    messages: Annotated[List[BaseMessage], operator.add]

    # ── Search criteria (immutable across cycles) ─────────────────────────────
    origin: str           # IATA code, e.g. "ICN"
    destination: str      # IATA code, e.g. "NRT"
    travel_date: str      # ISO date, e.g. "2026-06-15"
    max_price: float      # Price threshold in USD — book if cheaper
    currency: str         # Display currency ("USD")
    passenger_name: str   # Passenger name for the booking
    api_base_url: str     # Base URL of the flight search/booking API

    # ── Per-cycle state ────────────────────────────────────────────────────────
    check_number: int                  # Current monitoring iteration (1-indexed)
    active_phase: str                  # "search" | "price_analysis" | "booking" | "notification"

    # ── Populated by SearchAgent ───────────────────────────────────────────────
    available_flights: List[Dict]      # All flights returned by the search API
    cheapest_price: Optional[float]    # Price of the cheapest found flight
    cheapest_flight: Optional[Dict]    # Full flight object of the cheapest option

    # ── Populated by PriceAnalysisAgent ───────────────────────────────────────
    should_book: bool                  # True when cheapest_price < max_price

    # ── Populated by BookingAgent ──────────────────────────────────────────────
    booking_confirmed: bool            # True when the booking API returns CONFIRMED
    booking_reference: Optional[str]   # Booking confirmation code, e.g. "AGNT48271"
    confirmed_price: Optional[float]   # Final booked price
