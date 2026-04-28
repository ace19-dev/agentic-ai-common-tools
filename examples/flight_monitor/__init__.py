"""
Flight Monitor — Multi-Agent Example
=====================================
Demonstrates automatic flight price monitoring with multi-agent booking.

Usage:
  from examples.flight_monitor.run import run, MonitorCriteria
  run(MonitorCriteria(origin="ICN", destination="NRT", max_price=250.0))

CLI:
  python -m examples.flight_monitor.run --origin ICN --dest NRT --max-price 250

Agents:
  SearchAgent        → http_get + memory_set
  PriceAnalysisAgent → memory_get + structured LLM output
  BookingAgent       → http_post + memory_set + auth_get_key
  NotificationAgent  → notify_slack + notify_email + memory_set
"""
from .run import MonitorCriteria, run

__all__ = ["run", "MonitorCriteria"]
