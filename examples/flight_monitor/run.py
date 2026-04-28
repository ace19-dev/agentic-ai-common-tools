"""
Flight Monitor — Entry Point
==============================
An agentic AI system that continuously monitors flight prices and
automatically books + notifies when a cheap ticket is found.

Multi-agent composition for this scenario:
  SearchAgent        → Calls flight search API via http_get, stores results
  PriceAnalysisAgent → Reads results via memory_get, returns structured decision
  BookingAgent       → Calls booking API via http_post when price < threshold
  NotificationAgent  → Sends Slack + email alerts with booking confirmation

The monitoring loop calls the LangGraph workflow once per check cycle.
The mock API server simulates price fluctuations — cheap prices appear on
specific check numbers (default: 3rd and 6th checks).

Usage:
  python -m examples.flight_monitor.run
  python -m examples.flight_monitor.run --origin ICN --dest NRT --date 2026-07-01 --max-price 250

Environment:
  NOTIFICATION_DRY_RUN=true    (default — Slack/email printed to console)
  EMAIL_RECIPIENT=you@email.com
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from langchain_core.messages import HumanMessage

import config  # triggers dotenv + logging
from examples.flight_monitor.mock_api import MockFlightAPI
from examples.flight_monitor.workflow import build_flight_graph
from tools.auth_tools import auth_store_key

logger = logging.getLogger(__name__)

_MOCK_PORT = 18990
_BANNER = "=" * 65


@dataclass
class MonitorCriteria:
    origin: str = "ICN"
    destination: str = "NRT"
    travel_date: str = "2026-07-15"
    max_price: float = 280.0
    currency: str = "USD"
    passenger_name: str = "Agentic AI Traveler"
    check_interval_sec: int = 8     # seconds between checks (short for demo)
    max_checks: int = 10            # stop after this many checks regardless
    cheap_on_checks: list[int] = None  # which check numbers trigger a deal

    def __post_init__(self):
        if self.cheap_on_checks is None:
            self.cheap_on_checks = [3, 7]  # deals appear on 3rd and 7th checks


def _print_header(criteria: MonitorCriteria, api: MockFlightAPI) -> None:
    """Print a startup banner with all monitoring parameters."""
    print(_BANNER)
    print("✈️  FLIGHT MONITOR — AGENTIC AI")
    print(_BANNER)
    print(f"  Route       : {criteria.origin} → {criteria.destination}")
    print(f"  Date        : {criteria.travel_date}")
    print(f"  Max price   : ${criteria.max_price:.2f} {criteria.currency}")
    print(f"  Passenger   : {criteria.passenger_name}")
    print(f"  Interval    : every {criteria.check_interval_sec}s")
    print(f"  Max checks  : {criteria.max_checks}")
    print(f"  Deal checks : {criteria.cheap_on_checks} (mock simulation)")
    print(f"  API         : {api.base_url}")
    print(_BANNER)
    print()


def _print_cycle_header(check: int, total: int) -> None:
    """Print a separator line with the current check number and timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * 65}")
    print(f"  CHECK {check}/{total}  [{ts}]")
    print(f"{'─' * 65}")


def _print_result(result: dict) -> None:
    """Print a per-cycle summary: booking status, price, and last agent message."""
    booked = result.get("booking_confirmed", False)
    ref = result.get("booking_reference")
    price = result.get("confirmed_price") or result.get("cheapest_price")
    last_msg = result["messages"][-1].content if result.get("messages") else ""

    if booked:
        print(f"\n  ✅ BOOKED! Reference: {ref}  Price: ${price:.2f} USD")
    else:
        print(f"\n  ⏭  No deal this check. Cheapest: ${price:.2f} USD" if price else "  ⏭  No deal.")

    # Show last message content (truncated)
    if last_msg:
        print(f"\n  Agent summary:\n  {last_msg[:300]}")


def _build_initial_state(criteria: MonitorCriteria, api: MockFlightAPI, check: int) -> dict:
    """Construct a fresh FlightState for one monitoring cycle.

    A new state is created per cycle rather than mutating a shared state so
    each graph invocation is independent and side-effect-free at the state level.
    """
    task = (
        f"Monitor flight prices for {criteria.origin}→{criteria.destination} "
        f"on {criteria.travel_date}. "
        f"Maximum acceptable price: ${criteria.max_price} {criteria.currency}. "
        f"Passenger: {criteria.passenger_name}. "
        f"This is monitoring check #{check}."
    )
    return {
        "messages": [HumanMessage(content=task)],
        # Criteria
        "origin": criteria.origin,
        "destination": criteria.destination,
        "travel_date": criteria.travel_date,
        "max_price": criteria.max_price,
        "currency": criteria.currency,
        "passenger_name": criteria.passenger_name,
        "api_base_url": api.base_url,
        # Cycle state
        "check_number": check,
        "active_phase": "search",
        # Defaults
        "available_flights": [],
        "cheapest_price": None,
        "cheapest_flight": None,
        "should_book": False,
        "booking_confirmed": False,
        "booking_reference": None,
        "confirmed_price": None,
    }


def run(criteria: MonitorCriteria | None = None) -> dict | None:
    """
    Start the flight monitoring loop.

    Returns the final FlightState dict when a booking is made or max_checks
    is reached. Returns None if the loop ends without a booking.
    """
    if criteria is None:
        criteria = MonitorCriteria()

    # ── Start mock API ────────────────────────────────────────────────────────
    api = MockFlightAPI(port=_MOCK_PORT, cheap_on_checks=criteria.cheap_on_checks).start()
    _print_header(criteria, api)

    # ── Pre-store a fake API credential (demonstrates auth tool usage) ────────
    auth_store_key.invoke({"service": "flight-api", "key": "demo-api-key-xyz"})
    logger.info("API credential stored in encrypted vault")

    # ── Build the LangGraph workflow ──────────────────────────────────────────
    app = build_flight_graph()
    final_result = None

    try:
        for check in range(1, criteria.max_checks + 1):
            _print_cycle_header(check, criteria.max_checks)

            initial_state = _build_initial_state(criteria, api, check)

            try:
                result = app.invoke(initial_state)
            except Exception as exc:
                logger.error("Workflow error on check %d: %s", check, exc)
                print(f"  ⚠️  Workflow error: {exc}")
                if check < criteria.max_checks:
                    time.sleep(criteria.check_interval_sec)
                continue

            _print_result(result)

            if result.get("booking_confirmed"):
                final_result = result
                print(f"\n{_BANNER}")
                print("🎉  MONITORING COMPLETE — BOOKING CONFIRMED!")
                print(f"    Booking reference : {result['booking_reference']}")
                print(f"    Final price       : ${result.get('confirmed_price', 0):.2f} USD")
                print(f"    Savings           : ~${criteria.max_price - (result.get('confirmed_price') or 0):.2f} USD")
                print(f"    Found on check    : {check} of {criteria.max_checks}")
                print(_BANNER)
                break

            if check < criteria.max_checks:
                print(f"\n  ⏳ Next check in {criteria.check_interval_sec}s...")
                time.sleep(criteria.check_interval_sec)
        else:
            print(f"\n{_BANNER}")
            print(f"⚠️  MONITORING ENDED — no cheap flights found after {criteria.max_checks} checks.")
            print(f"    Increase max_price or try different dates.")
            print(_BANNER)

    finally:
        api.stop()

    return final_result


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> MonitorCriteria:
    """Parse CLI arguments and return a MonitorCriteria dataclass."""
    parser = argparse.ArgumentParser(
        description="Agentic flight price monitor — books automatically when price drops"
    )
    parser.add_argument("--origin",    default="ICN",        help="Origin IATA code (default: ICN)")
    parser.add_argument("--dest",      default="NRT",        help="Destination IATA code (default: NRT)")
    parser.add_argument("--date",      default="2026-07-15", help="Travel date YYYY-MM-DD")
    parser.add_argument("--max-price", type=float, default=280.0, help="Maximum acceptable price in USD")
    parser.add_argument("--passenger", default="Agentic AI Traveler", help="Passenger name")
    parser.add_argument("--interval",  type=int,   default=8,    help="Seconds between checks")
    parser.add_argument("--max-checks",type=int,   default=10,   help="Maximum number of checks before stopping")
    parser.add_argument("--cheap-on",  type=int,   nargs="+", default=[3, 7],
                        help="Which check numbers simulate cheap prices (mock only)")
    args = parser.parse_args()
    return MonitorCriteria(
        origin=args.origin,
        destination=args.dest,
        travel_date=args.date,
        max_price=args.max_price,
        passenger_name=args.passenger,
        check_interval_sec=args.interval,
        max_checks=args.max_checks,
        cheap_on_checks=args.cheap_on,
    )


if __name__ == "__main__":
    criteria = _parse_args()
    result = run(criteria)
    sys.exit(0 if result and result.get("booking_confirmed") else 1)
