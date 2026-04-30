"""
Flight Monitor — 멀티에이전트 예제
=====================================
멀티에이전트 자동 항공편 가격 모니터링 및 예약 시스템입니다.

사용법:
  from examples.flight_monitor.run import run, MonitorCriteria
  run(MonitorCriteria(origin="ICN", destination="NRT", max_price=250.0))

CLI:
  python -m examples.flight_monitor.run --origin ICN --dest NRT --max-price 250

에이전트:
  SearchAgent        → flight_search + memory_set
  PriceAnalysisAgent → 구조화 LLM 출력으로 예약 여부 결정
  BookingAgent       → flight_book + memory_set
  NotificationAgent  → notify_slack + notify_email + memory_set
"""
from .run import MonitorCriteria, run

__all__ = ["run", "MonitorCriteria"]
