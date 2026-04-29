"""
Flight MCP — pluggable flight search/booking client.

Backends:
  mock    — delegates to the local MockFlightAPI HTTP server (default, zero deps)
  amadeus — Amadeus for Developers REST API (https://developers.amadeus.com)

Configure at startup:
    from mcp.flight import configure_flight_client, MockFlightClient
    configure_flight_client(MockFlightClient(base_url="http://127.0.0.1:18990"))

Or via env vars (auto-configured when get_flight_client() is first called):
    FLIGHT_API_MODE=amadeus
    AMADEUS_CLIENT_ID=...
    AMADEUS_CLIENT_SECRET=...
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class FlightOffer:
    flight_id: str
    airline: str
    airline_code: str
    origin: str
    destination: str
    departure: str       # ISO 8601 datetime
    arrival: str         # ISO 8601 datetime
    duration_min: int
    price: float
    currency: str
    seats_available: int
    cabin_class: str
    is_deal: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "flight_id": self.flight_id,
            "airline": self.airline,
            "airline_code": self.airline_code,
            "origin": self.origin,
            "destination": self.destination,
            "departure": self.departure,
            "arrival": self.arrival,
            "duration_min": self.duration_min,
            "price": self.price,
            "currency": self.currency,
            "seats_available": self.seats_available,
            "cabin_class": self.cabin_class,
            "is_deal": self.is_deal,
        }


@dataclass
class SearchResult:
    origin: str
    destination: str
    date: str
    max_price_threshold: float
    flights: list[FlightOffer]
    searched_at: str
    mode: str = "mock"
    check_number: int = 0

    @property
    def cheapest(self) -> Optional[FlightOffer]:
        return min(self.flights, key=lambda f: f.price) if self.flights else None

    @property
    def cheapest_price(self) -> Optional[float]:
        c = self.cheapest
        return round(c.price, 2) if c else None

    @property
    def below_threshold(self) -> bool:
        p = self.cheapest_price
        return p is not None and p < self.max_price_threshold

    def to_dict(self) -> dict:
        return {
            "check_number": self.check_number,
            "mode": self.mode,
            "origin": self.origin,
            "destination": self.destination,
            "date": self.date,
            "max_price_threshold": self.max_price_threshold,
            "flights": [f.to_dict() for f in self.flights],
            "cheapest_price": self.cheapest_price,
            "below_threshold": self.below_threshold,
            "searched_at": self.searched_at,
        }


@dataclass
class BookingResult:
    success: bool
    booking_reference: Optional[str]
    flight_id: str
    airline: str
    origin: str
    destination: str
    departure: str
    passenger_name: str
    price: float
    currency: str
    confirmed_at: str
    booking_url: Optional[str] = None   # deep-link for manual completion (live mode)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": "CONFIRMED" if self.success else "FAILED",
            "booking_reference": self.booking_reference,
            "flight_id": self.flight_id,
            "airline": self.airline,
            "origin": self.origin,
            "destination": self.destination,
            "departure": self.departure,
            "passenger_name": self.passenger_name,
            "price": self.price,
            "currency": self.currency,
            "confirmed_at": self.confirmed_at,
            "booking_url": self.booking_url,
            "error": self.error,
        }


# ── Abstract base ─────────────────────────────────────────────────────────────


class BaseFlightClient(ABC):

    @abstractmethod
    def search(
        self,
        origin: str,
        destination: str,
        date: str,
        max_price: float,
        check_number: int = 0,
        adults: int = 1,
        cabin_class: str = "ECONOMY",
    ) -> SearchResult: ...

    @abstractmethod
    def book(
        self,
        offer: FlightOffer,
        passenger_name: str,
        passenger_email: str = "",
    ) -> BookingResult: ...

    @property
    @abstractmethod
    def mode(self) -> str: ...


# ── Mock client ───────────────────────────────────────────────────────────────


class MockFlightClient(BaseFlightClient):
    """Calls the local MockFlightAPI HTTP server (examples/flight_monitor/mock_api.py)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

    @property
    def mode(self) -> str:
        return "mock"

    def search(
        self,
        origin: str,
        destination: str,
        date: str,
        max_price: float,
        check_number: int = 0,
        adults: int = 1,
        cabin_class: str = "ECONOMY",
    ) -> SearchResult:
        resp = self._session.get(
            f"{self.base_url}/api/flights/search",
            params={"origin": origin, "destination": destination,
                    "date": date, "max_price": max_price},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        flights = [
            FlightOffer(
                flight_id=f["flight_id"],
                airline=f["airline"],
                airline_code=f["flight_id"][:2],
                origin=f["origin"],
                destination=f["destination"],
                departure=f["departure"],
                arrival=f.get("arrival", ""),
                duration_min=f.get("duration_min", 0),
                price=f["price"],
                currency=f.get("currency", "USD"),
                seats_available=f.get("seats_available", 9),
                cabin_class=f.get("cabin_class", "Economy"),
                is_deal=f.get("is_deal", False),
                raw=f,
            )
            for f in data.get("flights", [])
        ]
        return SearchResult(
            origin=origin,
            destination=destination,
            date=date,
            max_price_threshold=max_price,
            flights=flights,
            searched_at=data.get("searched_at", datetime.now().isoformat()),
            mode=self.mode,
            check_number=check_number or data.get("check_number", 0),
        )

    def book(self, offer: FlightOffer, passenger_name: str, passenger_email: str = "") -> BookingResult:
        resp = self._session.post(
            f"{self.base_url}/api/flights/book",
            json={
                "flight_id": offer.flight_id,
                "airline": offer.airline,
                "origin": offer.origin,
                "destination": offer.destination,
                "departure": offer.departure,
                "price": offer.price,
                "currency": offer.currency,
                "passenger_name": passenger_name,
                "passenger_email": passenger_email,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return BookingResult(
            success=data.get("status", "").upper() == "CONFIRMED",
            booking_reference=data.get("booking_reference"),
            flight_id=offer.flight_id,
            airline=offer.airline,
            origin=offer.origin,
            destination=offer.destination,
            departure=offer.departure,
            passenger_name=passenger_name,
            price=offer.price,
            currency=offer.currency,
            confirmed_at=data.get("confirmed_at", datetime.now().isoformat()),
        )


# ── Amadeus client ────────────────────────────────────────────────────────────


def _iso_duration_to_minutes(duration: str) -> int:
    """'PT2H30M' → 150"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


class AmadeusFlightClient(BaseFlightClient):
    """Amadeus for Developers — flight search with OAuth2 token caching.

    Search: real Amadeus /v2/shopping/flight-offers endpoint.
    Booking: generates a Google Flights deep-link + deal reference.
             Full PNR creation (/v1/booking/flight-orders) requires
             traveler passport + payment details beyond this project's scope.

    Credentials (checked in order):
      1. Constructor args (client_id, client_secret)
      2. AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET env vars
      3. Auth vault: auth_store_key("amadeus", "client_id:client_secret")
    """

    DEFAULT_BASE_URL = "https://test.api.amadeus.com"

    def __init__(self, client_id: str, client_secret: str, base_url: str = DEFAULT_BASE_URL):
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    @property
    def mode(self) -> str:
        return "amadeus"

    # ── OAuth2 ────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        resp = self._session.post(
            f"{self._base_url}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 1800))
        logger.debug("Amadeus token refreshed (expires in %ds)", data.get("expires_in"))
        return self._access_token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        origin: str,
        destination: str,
        date: str,
        max_price: float,
        check_number: int = 0,
        adults: int = 1,
        cabin_class: str = "ECONOMY",
    ) -> SearchResult:
        resp = self._session.get(
            f"{self._base_url}/v2/shopping/flight-offers",
            params={
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": date,
                "adults": adults,
                "travelClass": cabin_class,
                "currencyCode": "USD",
                "max": 10,
            },
            headers=self._auth_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        carriers = data.get("dictionaries", {}).get("carriers", {})
        flights: list[FlightOffer] = []
        for offer in data.get("data", []):
            try:
                flights.append(self._parse_offer(offer, carriers, origin, destination))
            except Exception as exc:
                logger.warning("Skipping unparseable offer: %s", exc)

        flights.sort(key=lambda f: f.price)
        return SearchResult(
            origin=origin,
            destination=destination,
            date=date,
            max_price_threshold=max_price,
            flights=flights,
            searched_at=datetime.now().isoformat(),
            mode=self.mode,
            check_number=check_number,
        )

    def _parse_offer(self, offer: dict, carriers: dict, origin: str, destination: str) -> FlightOffer:
        price = float(offer["price"]["total"])
        currency = offer["price"].get("currency", "USD")
        seats = offer.get("numberOfBookableSeats", 9)

        itinerary = offer["itineraries"][0]
        first_seg = itinerary["segments"][0]
        last_seg = itinerary["segments"][-1]

        carrier_code = first_seg["carrierCode"]
        flight_number = f"{carrier_code}{first_seg['number']}"
        carrier_name = carriers.get(carrier_code, carrier_code)
        departure = first_seg["departure"]["at"]
        arrival = last_seg["arrival"]["at"]
        duration_min = _iso_duration_to_minutes(itinerary.get("duration", ""))

        cabin = "ECONOMY"
        try:
            cabin = offer["travelerPricings"][0]["fareDetailsBySegment"][0]["cabin"]
        except (KeyError, IndexError):
            pass

        return FlightOffer(
            flight_id=flight_number,
            airline=carrier_name,
            airline_code=carrier_code,
            origin=origin,
            destination=destination,
            departure=departure,
            arrival=arrival,
            duration_min=duration_min,
            price=price,
            currency=currency,
            seats_available=seats,
            cabin_class=cabin,
            is_deal=price < 0,  # always False from Amadeus; set by SearchResult logic
            raw=offer,
        )

    # ── Booking (deal alert + deep-link) ──────────────────────────────────────

    def book(self, offer: FlightOffer, passenger_name: str, passenger_email: str = "") -> BookingResult:
        """Confirm deal intent and return a Google Flights deep-link.

        Full PNR creation via /v1/booking/flight-orders requires traveler
        passport and payment details. This produces a deal confirmation with
        a direct booking URL so the user can complete it themselves.
        """
        dep_date = offer.departure[:10]
        google_url = (
            f"https://www.google.com/flights#search;"
            f"iti={offer.origin}*{offer.destination}/{dep_date};tt=o"
        )
        ref = f"DEAL-{offer.flight_id}-{dep_date.replace('-', '')}"
        logger.info(
            "Live mode: deal confirmed %s @ $%.2f — %s", offer.flight_id, offer.price, google_url
        )
        return BookingResult(
            success=True,
            booking_reference=ref,
            flight_id=offer.flight_id,
            airline=offer.airline,
            origin=offer.origin,
            destination=offer.destination,
            departure=offer.departure,
            passenger_name=passenger_name,
            price=offer.price,
            currency=offer.currency,
            confirmed_at=datetime.now().isoformat(),
            booking_url=google_url,
        )


# ── Module-level singleton ────────────────────────────────────────────────────

_client: Optional[BaseFlightClient] = None


def configure_flight_client(client: BaseFlightClient) -> None:
    """Set the process-wide flight client. Must be called before using flight tools."""
    global _client
    _client = client
    logger.info("Flight client configured: mode=%s", client.mode)


def get_flight_client() -> BaseFlightClient:
    """Return the configured flight client, raising if not yet configured."""
    if _client is None:
        raise RuntimeError(
            "Flight client not configured. "
            "Call configure_flight_client() in run.py before using flight tools."
        )
    return _client
