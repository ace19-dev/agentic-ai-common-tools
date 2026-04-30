"""
Flight Monitor — 진입점
========================
저렴한 항공권을 발견하면 자동으로 예약(또는 딜 확인)하는
에이전틱 AI 항공편 가격 모니터링 시스템입니다.

모드:
  mock    (기본값) — 로컬 MockFlightAPI HTTP 서버; --cheap-on으로 지정한
                     체크 번호에서 딜이 등장합니다.
  amadeus           — 실제 Amadeus for Developers API; 실행 전
                      환경 변수 또는 auth vault에 자격증명을 설정하세요.

멀티에이전트 구성:
  SearchAgent        → flight_search 호출, 결과를 메모리에 저장
  PriceAnalysisAgent → 결과 읽기, 예약 여부 결정 (구조화 출력)
  BookingAgent       → 가격 < 임계값이면 flight_book 호출
  NotificationAgent  → Slack + 이메일 알림 발송

사용법:
  # Mock 모드 (설정 없이 바로 실행)
  python -m examples.flight_monitor.run

  # Mock 모드 — 커스텀 노선
  python -m examples.flight_monitor.run --origin ICN --dest BKK --date 2026-08-01 --max-price 350

  # Live 모드 — Amadeus (.env에 AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET 설정 후)
  python -m examples.flight_monitor.run --mode amadeus --origin ICN --dest NRT --date 2026-07-15

  # Live 모드 — auth vault에서 자격증명 로드
  python -m examples.flight_monitor.run --mode amadeus --amadeus-key amadeus

환경 변수:
  NOTIFICATION_DRY_RUN=true     (기본값 — Slack/이메일이 콘솔에 출력됨)
  EMAIL_RECIPIENT=you@email.com
  AMADEUS_CLIENT_ID=...
  AMADEUS_CLIENT_SECRET=...
  AMADEUS_BASE_URL=https://test.api.amadeus.com   (실제 예약 시 프로덕션 URL로 교체)
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
    # Mock 모드 전용 — 딜이 발생할 체크 번호
    cheap_on_checks: list[int] = field(default_factory=lambda: [3, 7])
    # API 모드: "mock" | "amadeus"
    mode: str = "mock"
    # Amadeus 자격증명의 auth vault 서비스 이름 (mode=amadeus 전용)
    amadeus_key_service: str = "amadeus"


# ── Amadeus 자격증명 로더 ─────────────────────────────────────────────────────


def _load_amadeus_client(criteria: MonitorCriteria) -> AmadeusFlightClient:
    """auth vault 또는 환경 변수에서 Amadeus 자격증명을 로드하고 클라이언트를 반환합니다."""
    client_id = config.AMADEUS_CLIENT_ID
    client_secret = config.AMADEUS_CLIENT_SECRET

    if not (client_id and client_secret):
        # auth vault 시도: "client_id:client_secret" 형식으로 저장되어 있음
        try:
            from mcp.auth import get_auth_mcp
            auth_result = get_auth_mcp().retrieve(criteria.amadeus_key_service)
            if auth_result.success and ":" in (auth_result.data or ""):
                client_id, client_secret = auth_result.data.split(":", 1)
                logger.info("auth vault에서 Amadeus 자격증명 로드 완료 (%s)", criteria.amadeus_key_service)
        except Exception as exc:
            logger.warning("vault에서 Amadeus 자격증명을 로드할 수 없습니다: %s", exc)

    if not (client_id and client_secret):
        print(
            "\n[ERROR] Amadeus 자격증명을 찾을 수 없습니다.\n"
            "  방법 A — .env 파일에 설정:\n"
            "    AMADEUS_CLIENT_ID=<id>\n"
            "    AMADEUS_CLIENT_SECRET=<secret>\n\n"
            "  방법 B — auth vault에 저장:\n"
            "    from tools.auth_tools import auth_store_key\n"
            "    auth_store_key.invoke({'service': 'amadeus', 'key': '<id>:<secret>'})\n"
        )
        sys.exit(1)

    return AmadeusFlightClient(
        client_id=client_id,
        client_secret=client_secret,
        base_url=config.AMADEUS_BASE_URL,
    )


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────


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


# ── 초기 state 빌더 ───────────────────────────────────────────────────────────


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
        # 검색 조건
        "origin": criteria.origin,
        "destination": criteria.destination,
        "travel_date": criteria.travel_date,
        "max_price": criteria.max_price,
        "currency": criteria.currency,
        "passenger_name": criteria.passenger_name,
        "passenger_email": criteria.passenger_email,
        "api_base_url": api_base_url,
        # 사이클 상태
        "check_number": check,
        "active_phase": "search",
        # 기본값
        "available_flights": [],
        "cheapest_price": None,
        "cheapest_flight": None,
        "should_book": False,
        "booking_confirmed": False,
        "booking_reference": None,
        "confirmed_price": None,
        "booking_url": None,
    }


# ── 메인 루프 ─────────────────────────────────────────────────────────────────


def run(criteria: MonitorCriteria | None = None) -> dict | None:
    """항공편 모니터링 루프를 시작합니다.

    예약이 확인되거나 max_checks에 도달하면 최종 FlightState dict를 반환합니다.
    예약이 없으면 None을 반환합니다.
    """
    if criteria is None:
        criteria = MonitorCriteria()

    # ── 항공편 클라이언트 설정 ────────────────────────────────────────────────
    mock_api = None
    api_base_url = ""

    if criteria.mode == "mock":
        from examples.flight_monitor.mock_api import MockFlightAPI
        mock_api = MockFlightAPI(port=18990, cheap_on_checks=criteria.cheap_on_checks).start()
        api_base_url = mock_api.base_url
        configure_flight_client(MockFlightClient(base_url=api_base_url))
        logger.info("Mock 항공편 API 시작: %s", api_base_url)
    else:
        amadeus = _load_amadeus_client(criteria)
        configure_flight_client(amadeus)
        logger.info("Amadeus 항공편 클라이언트 설정 완료 (base_url=%s)", config.AMADEUS_BASE_URL)

    _print_header(criteria)

    # ── LangGraph 워크플로우 빌드 ─────────────────────────────────────────────
    app = build_flight_graph()
    final_result = None

    try:
        for check in range(1, criteria.max_checks + 1):
            _print_cycle_header(check, criteria.max_checks)

            initial_state = _build_initial_state(criteria, api_base_url, check)

            try:
                result = app.invoke(initial_state)
            except Exception as exc:
                logger.error("체크 %d 워크플로우 오류: %s", check, exc)
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
        description="에이전틱 항공편 가격 모니터 — 가격 하락 시 자동 예약",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Mock 모드 (기본값, 설정 없이 실행)
  python -m examples.flight_monitor.run

  # Mock — 커스텀 노선 및 딜 시뮬레이션
  python -m examples.flight_monitor.run --origin ICN --dest BKK --date 2026-08-01 --max-price 350 --cheap-on 2 5

  # Live Amadeus 모드 (.env에서 자격증명 로드)
  python -m examples.flight_monitor.run --mode amadeus --origin ICN --dest NRT --date 2026-07-15

  # Live Amadeus 모드 (auth vault에서 자격증명 로드)
  python -m examples.flight_monitor.run --mode amadeus --amadeus-key amadeus
        """,
    )
    parser.add_argument("--mode", choices=["mock", "amadeus"], default="mock",
                        help="API 백엔드: 'mock' (기본값) 또는 'amadeus' (실제 Amadeus API)")
    parser.add_argument("--origin",       default="ICN",                  help="출발지 IATA 코드 (기본값: ICN)")
    parser.add_argument("--dest",         default="NRT",                  help="목적지 IATA 코드 (기본값: NRT)")
    parser.add_argument("--date",         default="2026-07-15",           help="여행 날짜 YYYY-MM-DD")
    parser.add_argument("--max-price",    type=float, default=280.0,      help="최대 허용 가격 (USD)")
    parser.add_argument("--passenger",    default="Agentic AI Traveler",  help="승객 이름")
    parser.add_argument("--email",        default="",                     help="승객 이메일 (예약 확인 발송용)")
    parser.add_argument("--interval",     type=int,   default=8,          help="체크 간격 (초)")
    parser.add_argument("--max-checks",   type=int,   default=10,         help="최대 체크 횟수")
    parser.add_argument("--cheap-on",     type=int, nargs="+", default=[3, 7],
                        help="저렴한 가격을 시뮬레이션할 체크 번호 (mock 모드 전용)")
    parser.add_argument("--amadeus-key",  default="amadeus",
                        help="Amadeus 자격증명의 auth vault 서비스 이름 (기본값: 'amadeus')")
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
