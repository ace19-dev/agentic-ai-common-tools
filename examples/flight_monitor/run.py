"""
Flight Monitor — Entry Point
==============================
An agentic AI system that continuously monitors flight prices and
automatically books (or confirms a deal) when a cheap ticket is found.

Modes:
  mock    (default) — local MockFlightAPI HTTP server; deals appear on
                      check numbers specified by --cheap-on.
  amadeus           — real Amadeus for Developers API; set credentials via
                      env vars or auth vault before running.

Multi-agent composition:
  SearchAgent        → Calls flight_search, stores results in memory
  PriceAnalysisAgent → Reads results, decides whether to book (structured output)
  BookingAgent       → Calls flight_book when price < threshold
  NotificationAgent  → Sends Slack + email alerts with outcome

Usage:
  # Mock mode (zero config)
  python -m examples.flight_monitor.run

  # Mock mode — custom route
  python -m examples.flight_monitor.run --origin ICN --dest BKK --date 2026-08-01 --max-price 350

  # Live mode — Amadeus (set AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET in .env first)
  python -m examples.flight_monitor.run --mode amadeus --origin ICN --dest NRT --date 2026-07-15

  # Live mode — credentials from auth vault
  python -m examples.flight_monitor.run --mode amadeus --amadeus-key amadeus

Environment:
  NOTIFICATION_DRY_RUN=true     (default — Slack/email printed to console)
  EMAIL_RECIPIENT=you@email.com
  AMADEUS_CLIENT_ID=...
  AMADEUS_CLIENT_SECRET=...
  AMADEUS_BASE_URL=https://test.api.amadeus.com   (switch to production URL for live bookings)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import HumanMessage

import config
from examples.flight_monitor.workflow import build_flight_graph
from mcp.flight import (
    AmadeusFlightClient,
    MockFlightClient,
    configure_flight_client,
)

logger = logging.getLogger(__name__)

_BANNER = "=" * 65


# ── MonitorCriteria ───────────────────────────────────────────────────────────


@dataclass
class MonitorCriteria:
    origin: str = "ICN"
    destination: str = "NRT"
    travel_date: str = "2026-07-15"
    max_price: float = 280.0
    currency: str = "USD"
    passenger_name: str = "Agentic AI Traveler"
    passenger_email: str = ""
    check_interval_sec: int = 8
    max_checks: int = 10
    # Mock-mode only — which check numbers trigger a deal
    cheap_on_checks: list[int] = field(default_factory=lambda: [3, 7])
    # API mode: "mock" | "amadeus"
    mode: str = "mock"
    # Auth vault service name for Amadeus credentials (mode=amadeus only)
    amadeus_key_service: str = "amadeus"


# ── Amadeus credential loader ─────────────────────────────────────────────────


def _load_amadeus_client(criteria: MonitorCriteria) -> AmadeusFlightClient:
    """Load Amadeus credentials from auth vault or env vars and return a client."""
    client_id = config.AMADEUS_CLIENT_ID
    client_secret = config.AMADEUS_CLIENT_SECRET

    if not (client_id and client_secret):
        # Try auth vault: stored as "client_id:client_secret"
        try:
            from mcp.auth import get_auth_mcp
            auth_result = get_auth_mcp().retrieve(criteria.amadeus_key_service)
            if auth_result.success and ":" in (auth_result.data or ""):
                client_id, client_secret = auth_result.data.split(":", 1)
                logger.info("Amadeus credentials loaded from auth vault (%s)", criteria.amadeus_key_service)
        except Exception as exc:
            logger.warning("Could not load Amadeus credentials from vault: %s", exc)

    if not (client_id and client_secret):
        print(
            "\n[ERROR] Amadeus credentials not found.\n"
            "  Option A — set in .env:\n"
            "    AMADEUS_CLIENT_ID=<id>\n"
            "    AMADEUS_CLIENT_SECRET=<secret>\n\n"
            "  Option B — store in auth vault:\n"
            "    from tools.auth_tools import auth_store_key\n"
            "    auth_store_key.invoke({'service': 'amadeus', 'key': '<id>:<secret>'})\n"
        )
        sys.exit(1)

    return AmadeusFlightClient(
        client_id=client_id,
        client_secret=client_secret,
        base_url=config.AMADEUS_BASE_URL,
    )


# ── Print helpers ─────────────────────────────────────────────────────────────


def _print_header(criteria: MonitorCriteria) -> None:
    print(_BANNER)
    print("✈️  FLIGHT MONITOR — AGENTIC AI")
    print(_BANNER)
    print(f"  Mode        : {criteria.mode.upper()}")
    print(f"  Route       : {criteria.origin} → {criteria.destination}")
    print(f"  Date        : {criteria.travel_date}")
    print(f"  Max price   : ${criteria.max_price:.2f} {criteria.currency}")
    print(f"  Passenger   : {criteria.passenger_name}")
    if criteria.passenger_email:
        print(f"  Email       : {criteria.passenger_email}")
    print(f"  Interval    : every {criteria.check_interval_sec}s")
    print(f"  Max checks  : {criteria.max_checks}")
    if criteria.mode == "mock":
        print(f"  Deal checks : {criteria.cheap_on_checks} (mock simulation)")
    print(_BANNER)
    print()


def _print_cycle_header(check: int, total: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * 65}")
    print(f"  CHECK {check}/{total}  [{ts}]")
    print(f"{'─' * 65}")


def _print_result(result: dict, max_price: float) -> None:
    booked = result.get("booking_confirmed", False)
    ref = result.get("booking_reference")
    price = result.get("confirmed_price") or result.get("cheapest_price")
    booking_url = result.get("booking_url")
    last_msg = result["messages"][-1].content if result.get("messages") else ""

    if booked:
        print(f"\n  ✅ BOOKED! Reference: {ref}  Price: ${price:.2f} USD")
        if booking_url:
            print(f"  🔗 Complete booking: {booking_url}")
    else:
        if price:
            print(f"\n  ⏭  No deal this check. Cheapest: ${price:.2f} USD")
        else:
            print("\n  ⏭  No deal.")

    if last_msg:
        print(f"\n  Agent summary:\n  {last_msg[:300]}")


# ── State builder ─────────────────────────────────────────────────────────────


def _build_initial_state(criteria: MonitorCriteria, api_base_url: str, check: int) -> dict:
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
        "passenger_email": criteria.passenger_email,
        "api_base_url": api_base_url,
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
        "booking_url": None,
    }


# ── Main loop ─────────────────────────────────────────────────────────────────


def run(criteria: MonitorCriteria | None = None) -> dict | None:
    """Start the flight monitoring loop.

    Returns the final FlightState dict when a booking is confirmed or
    max_checks is reached. Returns None if no booking was made.
    """
    if criteria is None:
        criteria = MonitorCriteria()

    # ── Configure the flight client ───────────────────────────────────────────
    mock_api = None
    api_base_url = ""

    if criteria.mode == "mock":
        from examples.flight_monitor.mock_api import MockFlightAPI
        mock_api = MockFlightAPI(port=18990, cheap_on_checks=criteria.cheap_on_checks).start()
        api_base_url = mock_api.base_url
        configure_flight_client(MockFlightClient(base_url=api_base_url))
        logger.info("Mock flight API started at %s", api_base_url)
    else:
        amadeus = _load_amadeus_client(criteria)
        configure_flight_client(amadeus)
        logger.info("Amadeus flight client configured (base_url=%s)", config.AMADEUS_BASE_URL)

    _print_header(criteria)

    # ── Build the LangGraph workflow ──────────────────────────────────────────
    app = build_flight_graph()
    final_result = None

    try:
        for check in range(1, criteria.max_checks + 1):
            _print_cycle_header(check, criteria.max_checks)

            initial_state = _build_initial_state(criteria, api_base_url, check)

            try:
                result = app.invoke(initial_state)
            except Exception as exc:
                logger.error("Workflow error on check %d: %s", check, exc)
                print(f"  ⚠️  Workflow error: {exc}")
                if check < criteria.max_checks:
                    time.sleep(criteria.check_interval_sec)
                continue

            _print_result(result, criteria.max_price)

            if result.get("booking_confirmed"):
                final_result = result
                price = result.get("confirmed_price", 0) or 0
                print(f"\n{_BANNER}")
                print("🎉  MONITORING COMPLETE — BOOKING CONFIRMED!")
                print(f"    Booking reference : {result['booking_reference']}")
                print(f"    Final price       : ${price:.2f} USD")
                print(f"    Savings           : ~${criteria.max_price - price:.2f} USD")
                print(f"    Found on check    : {check} of {criteria.max_checks}")
                if result.get("booking_url"):
                    print(f"    Complete booking  : {result['booking_url']}")
                print(_BANNER)
                break

            if check < criteria.max_checks:
                print(f"\n  ⏳ Next check in {criteria.check_interval_sec}s...")
                time.sleep(criteria.check_interval_sec)
        else:
            print(f"\n{_BANNER}")
            print(f"⚠️  MONITORING ENDED — no cheap flights found after {criteria.max_checks} checks.")
            print(f"    Increase --max-price or try different dates.")
            print(_BANNER)

    finally:
        if mock_api:
            mock_api.stop()

    return final_result


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> MonitorCriteria:
    parser = argparse.ArgumentParser(
        description="Agentic flight price monitor — books automatically when price drops",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Mock mode (default, zero config)
  python -m examples.flight_monitor.run

  # Mock — custom route and deal simulation
  python -m examples.flight_monitor.run --origin ICN --dest BKK --date 2026-08-01 --max-price 350 --cheap-on 2 5

  # Live Amadeus mode (credentials from .env)
  python -m examples.flight_monitor.run --mode amadeus --origin ICN --dest NRT --date 2026-07-15

  # Live Amadeus mode (credentials from auth vault)
  python -m examples.flight_monitor.run --mode amadeus --amadeus-key amadeus
        """,
    )
    parser.add_argument("--mode", choices=["mock", "amadeus"], default="mock",
                        help="API backend: 'mock' (default) or 'amadeus' (real Amadeus API)")
    parser.add_argument("--origin",       default="ICN",                  help="Origin IATA code (default: ICN)")
    parser.add_argument("--dest",         default="NRT",                  help="Destination IATA code (default: NRT)")
    parser.add_argument("--date",         default="2026-07-15",           help="Travel date YYYY-MM-DD")
    parser.add_argument("--max-price",    type=float, default=280.0,      help="Maximum acceptable price in USD")
    parser.add_argument("--passenger",    default="Agentic AI Traveler",  help="Passenger name")
    parser.add_argument("--email",        default="",                     help="Passenger email (for booking confirmation)")
    parser.add_argument("--interval",     type=int,   default=8,          help="Seconds between checks")
    parser.add_argument("--max-checks",   type=int,   default=10,         help="Maximum number of checks")
    parser.add_argument("--cheap-on",     type=int, nargs="+", default=[3, 7],
                        help="Check numbers that simulate cheap prices (mock mode only)")
    parser.add_argument("--amadeus-key",  default="amadeus",
                        help="Auth vault service name for Amadeus credentials (default: 'amadeus')")
    args = parser.parse_args()

    return MonitorCriteria(
        mode=args.mode,
        origin=args.origin,
        destination=args.dest,
        travel_date=args.date,
        max_price=args.max_price,
        passenger_name=args.passenger,
        passenger_email=args.email,
        check_interval_sec=args.interval,
        max_checks=args.max_checks,
        cheap_on_checks=args.cheap_on,
        amadeus_key_service=args.amadeus_key,
    )


if __name__ == "__main__":
    criteria = _parse_args()
    result = run(criteria)
    sys.exit(0 if result and result.get("booking_confirmed") else 1)
