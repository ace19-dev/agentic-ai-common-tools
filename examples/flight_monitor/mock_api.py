"""Mock flight search and booking API.

Runs as a background thread using Python's built-in HTTP server.
Simulates realistic price fluctuations: most checks return prices above
the threshold; specified check numbers return cheap deals.

Endpoints:
  GET  /api/flights/search?origin=ICN&destination=NRT&date=...&max_price=300
  POST /api/flights/book        { flight_id, airline, price, passenger_name, ... }
  GET  /api/health
"""
from __future__ import annotations

import http.server
import json
import random
import threading
from datetime import datetime
from urllib.parse import parse_qs, urlparse

_AIRLINES = [
    {"code": "KE", "name": "Korean Air"},
    {"code": "OZ", "name": "Asiana Airlines"},
    {"code": "7C", "name": "Jeju Air"},
    {"code": "BX", "name": "Air Busan"},
    {"code": "LJ", "name": "Jin Air"},
]

# Mutable server state — reset by MockFlightAPI.reset()
_state: dict = {"check_count": 0, "cheap_on": set()}


class _FlightHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        """Dispatch GET requests to /api/flights/search or /api/health."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/flights/search":
            self._search(qs)
        elif parsed.path == "/api/health":
            self._json(200, {"status": "ok", "check_count": _state["check_count"]})
        else:
            self._json(404, {"error": "endpoint not found"})

    def do_POST(self) -> None:
        """Dispatch POST requests to /api/flights/book."""
        if self.path == "/api/flights/book":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._book(body)
        else:
            self._json(404, {"error": "endpoint not found"})

    # ── Route handlers ────────────────────────────────────────────────────────

    def _search(self, qs: dict) -> None:
        """Simulate flight search with realistic price variation.

        On check numbers listed in _state["cheap_on"], the first airline in
        the randomly-sampled set receives a price below the max_price threshold
        (58–79 % of it) to simulate a deal becoming available.
        """
        _state["check_count"] += 1
        count = _state["check_count"]

        origin = _get(qs, "origin", "ICN")
        destination = _get(qs, "destination", "NRT")
        date = _get(qs, "date", "2026-06-01")
        max_price = float(_get(qs, "max_price", "300"))

        is_deal_check = count in _state.get("cheap_on", set())

        airlines = random.sample(_AIRLINES, min(4, len(_AIRLINES)))
        flights = []
        for i, airline in enumerate(airlines):
            # On deal checks, the first airline gets a price well below threshold
            if is_deal_check and i == 0:
                price = max_price * random.uniform(0.58, 0.79)
                deal_flag = True
            else:
                price = max_price * random.uniform(1.06, 1.52)
                deal_flag = False

            flights.append({
                "flight_id": f"{airline['code']}{random.randint(100, 999)}",
                "airline": airline["name"],
                "origin": origin,
                "destination": destination,
                "departure": f"{date}T{8 + i * 2:02d}:30:00+09:00",
                "arrival": f"{date}T{11 + i * 2:02d}:15:00+09:00",
                "duration_min": 155 + i * 10,
                "price": round(price, 2),
                "currency": "USD",
                "seats_available": random.randint(2, 14),
                "cabin_class": "Economy",
                "is_deal": deal_flag,
            })

        flights.sort(key=lambda f: f["price"])
        cheapest_price = flights[0]["price"]

        self._json(200, {
            "check_number": count,
            "origin": origin,
            "destination": destination,
            "date": date,
            "max_price_threshold": max_price,
            "flights": flights,
            "cheapest_price": cheapest_price,
            "below_threshold": cheapest_price < max_price,
            "searched_at": datetime.now().isoformat(),
        })

    def _book(self, body: dict) -> None:
        """Always confirm the booking and return a random reference number."""
        self._json(200, {
            "booking_reference": f"AGNT{random.randint(10000, 99999)}",
            "status": "CONFIRMED",
            "flight_id": body.get("flight_id", "UNKNOWN"),
            "airline": body.get("airline", ""),
            "origin": body.get("origin", ""),
            "destination": body.get("destination", ""),
            "departure": body.get("departure", ""),
            "passenger_name": body.get("passenger_name", "Agentic AI Traveler"),
            "price": body.get("price", 0),
            "currency": body.get("currency", "USD"),
            "confirmed_at": datetime.now().isoformat(),
        })

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _json(self, status: int, data: dict) -> None:
        """Write a JSON response with the given HTTP status code."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # suppress default access log


def _get(qs: dict, key: str, default: str) -> str:
    vals = qs.get(key, [default])
    return vals[0] if vals else default


# ── Public API ────────────────────────────────────────────────────────────────

class MockFlightAPI:
    """Lightweight mock HTTP server for flight search and booking.

    Usage:
        api = MockFlightAPI(port=18990, cheap_on_checks=[3, 6]).start()
        print(api.base_url)  # http://127.0.0.1:18990
        ...
        api.stop()
    """

    def __init__(self, port: int = 18990, cheap_on_checks: list[int] | None = None):
        _state["check_count"] = 0
        _state["cheap_on"] = set(cheap_on_checks or [3, 6])
        self.port = port
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), _FlightHandler
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "MockFlightAPI":
        """Start the server in a daemon thread and return self for chaining."""
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="mock-flight-api"
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut down the HTTP server; blocks until the server thread exits."""
        if self._server:
            self._server.shutdown()

    def reset(self) -> None:
        """Reset the check counter (useful between test runs)."""
        _state["check_count"] = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def check_count(self) -> int:
        return _state["check_count"]
