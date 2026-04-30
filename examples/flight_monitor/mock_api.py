"""Mock 항공편 검색 및 예약 API.

Python 내장 HTTP 서버를 사용해 백그라운드 스레드로 실행됩니다.
대부분의 체크에서는 임계값 이상의 가격을 반환하고,
지정된 체크 번호에서는 저렴한 딜이 등장하는 현실적인 가격 변동을 시뮬레이션합니다.

엔드포인트:
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

# 변경 가능한 서버 상태 — MockFlightAPI.reset()으로 초기화
_state: dict = {"check_count": 0, "cheap_on": set()}


class _FlightHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        """/api/flights/search 또는 /api/health로 GET 요청을 디스패치합니다."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/flights/search":
            self._search(qs)
        elif parsed.path == "/api/health":
            self._json(200, {"status": "ok", "check_count": _state["check_count"]})
        else:
            self._json(404, {"error": "endpoint not found"})

    def do_POST(self) -> None:
        """/api/flights/book으로 POST 요청을 디스패치합니다."""
        if self.path == "/api/flights/book":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._book(body)
        else:
            self._json(404, {"error": "endpoint not found"})

    # ── 라우트 핸들러 ─────────────────────────────────────────────────────────

    def _search(self, qs: dict) -> None:
        """현실적인 가격 변동을 포함한 항공편 검색을 시뮬레이션합니다.

        _state["cheap_on"]에 지정된 체크 번호에서는 무작위로 샘플링된 항공사 중
        첫 번째 항공사가 max_price의 58~79% 가격을 받아 딜이 등장하는 것처럼 보입니다.
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
            # 딜 체크에서는 첫 번째 항공사가 임계값보다 훨씬 낮은 가격을 받음
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
        """항상 예약을 확인하고 무작위 참조 번호를 반환합니다."""
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

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────

    def _json(self, status: int, data: dict) -> None:
        """주어진 HTTP 상태 코드와 함께 JSON 응답을 씁니다."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # 기본 접근 로그 억제


def _get(qs: dict, key: str, default: str) -> str:
    vals = qs.get(key, [default])
    return vals[0] if vals else default


# ── 공개 API ──────────────────────────────────────────────────────────────────

class MockFlightAPI:
    """항공편 검색 및 예약을 위한 경량 Mock HTTP 서버.

    사용법:
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
        """데몬 스레드에서 서버를 시작하고 메서드 체이닝을 위해 self를 반환합니다."""
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="mock-flight-api"
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """HTTP 서버를 종료합니다. 서버 스레드가 끝날 때까지 블로킹됩니다."""
        if self._server:
            self._server.shutdown()

    def reset(self) -> None:
        """체크 카운터를 초기화합니다 (테스트 실행 사이에 유용합니다)."""
        _state["check_count"] = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def check_count(self) -> int:
        return _state["check_count"]
