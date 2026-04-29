"""
LangChain @tool wrappers for flight search and booking.

Backed by whichever FlightClient is configured at startup (mock or Amadeus).
Agents call these tools without knowing the active backend.
"""
from __future__ import annotations

import json

from langchain_core.tools import tool

from mcp.flight import FlightOffer, get_flight_client


@tool
def flight_search(
    origin: str,
    destination: str,
    date: str,
    max_price: float,
    check_number: int = 0,
    adults: int = 1,
) -> str:
    """Search for available flights between two airports.

    Args:
        origin: Departure airport IATA code (e.g. 'ICN').
        destination: Arrival airport IATA code (e.g. 'NRT').
        date: Travel date in YYYY-MM-DD format.
        max_price: Maximum acceptable price in USD — flags below_threshold.
        check_number: Current monitoring iteration (for logging context).
        adults: Number of adult passengers (default 1).

    Returns:
        JSON string with flights list, cheapest_price, below_threshold flag,
        and mode ('mock' | 'amadeus').
    """
    try:
        result = get_flight_client().search(
            origin=origin,
            destination=destination,
            date=date,
            max_price=max_price,
            check_number=check_number,
            adults=adults,
        )
        return json.dumps(result.to_dict(), ensure_ascii=False)
    except Exception as exc:
        return f"ERROR: flight_search failed — {exc}"


@tool
def flight_book(
    flight_id: str,
    airline: str,
    origin: str,
    destination: str,
    departure: str,
    price: float,
    passenger_name: str,
    passenger_email: str = "",
    currency: str = "USD",
) -> str:
    """Book a specific flight identified from search results.

    In mock mode: returns a CONFIRMED booking reference.
    In live (Amadeus) mode: returns a deal reference + Google Flights deep-link
    so the passenger can complete the booking directly.

    Args:
        flight_id: Flight identifier from search (e.g. 'KE703').
        airline: Full airline name (e.g. 'Korean Air').
        origin: Departure IATA code.
        destination: Arrival IATA code.
        departure: Departure datetime (ISO 8601).
        price: Total price in USD.
        passenger_name: Full passenger name.
        passenger_email: Contact email for confirmation (optional).
        currency: Currency code (default 'USD').

    Returns:
        JSON with status, booking_reference, confirmed_at, and booking_url if available.
    """
    try:
        offer = FlightOffer(
            flight_id=flight_id,
            airline=airline,
            airline_code=flight_id[:2] if len(flight_id) >= 2 else "",
            origin=origin,
            destination=destination,
            departure=departure,
            arrival="",
            duration_min=0,
            price=price,
            currency=currency,
            seats_available=1,
            cabin_class="ECONOMY",
        )
        result = get_flight_client().book(offer, passenger_name, passenger_email)
        return json.dumps(result.to_dict(), ensure_ascii=False)
    except Exception as exc:
        return f"ERROR: flight_book failed — {exc}"
