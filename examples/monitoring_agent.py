"""
Monitoring Agent Example
========================
Scenario:
  Given a list of target URLs, the agent:
    1. Performs an HTTP GET health check on each target.
    2. Stores each result in memory under namespace 'monitoring'.
    3. Sends a Slack alert for every target that returns a non-200 status code.
    4. Posts a summary report to Slack #monitoring.
    5. Optionally schedules a recurring health check job (if APScheduler is available).

Tools demonstrated:
  http_get, memory_set, memory_list_keys, notify_slack, notify_console, schedule_create

Usage:
  python -m examples.monitoring_agent
  python -m examples.monitoring_agent https://httpbin.org/status/200 https://httpbin.org/status/503

Environment:
  NOTIFICATION_DRY_RUN=true  (safe default — prints Slack alerts to console)
  MONITORING_TARGETS         — comma-separated list of URLs to check
"""
from __future__ import annotations

import os
import sys

from langchain_core.messages import HumanMessage

import config
from graph.workflow import build_graph
from mcp.scheduler import get_scheduler_mcp

DEFAULT_TARGETS = [
    "https://httpbin.org/status/200",
    "https://httpbin.org/status/503",
    "https://httpbin.org/status/200",
]


def _register_health_check_function() -> None:
    """Register health_check_all with the SchedulerMCP whitelist.

    Must be called before any agent tries to schedule this function by name.
    The inner closure captures DEFAULT_TARGETS as a fallback so the scheduled
    job works even when called without explicit kwargs.
    """
    scheduler = get_scheduler_mcp()

    def health_check_all(**kwargs):
        import requests
        targets = kwargs.get("targets", DEFAULT_TARGETS)
        for url in targets:
            try:
                r = requests.get(url, timeout=5)
                status = "OK" if r.ok else f"FAIL ({r.status_code})"
            except Exception as e:
                status = f"ERROR ({e})"
            print(f"[SCHEDULED CHECK] {url}: {status}")

    scheduler.register("health_check_all", health_check_all)


_TASK_TEMPLATE = """
You are a monitoring agent performing health checks. Execute all steps:

Step 1 – For each target URL, call http_get to check its HTTP status:
{target_list}

Step 2 – Store each result in memory (namespace "monitoring"):
  key format: "health_<url_slug>" where url_slug replaces '://', '/', '.', '-' with '_'
  value: JSON string with keys "url", "status_code", "ok", "checked_at" (use current time description)

Step 3 – For each target where ok=false or status_code != 200:
  Call notify_slack with channel="#alerts",
  message="🚨 ALERT: <url> returned HTTP <status_code>"

Step 4 – Count total targets and healthy targets, then send a summary:
  Call notify_slack with channel="#monitoring",
  message="Health check complete: <healthy>/<total> targets healthy"

Step 5 – Log the overall result:
  Call notify_console with level="INFO",
  message="Monitoring run complete. Checked <total> targets."
"""


def run(targets: list[str] | None = None) -> dict:
    """Check all target URLs and send Slack alerts for any that are unhealthy.

    Target priority: explicit argument → MONITORING_TARGETS env var → DEFAULT_TARGETS.

    Args:
        targets: URLs to health-check.  Pass None to use env/default targets.

    Returns:
        Final AgentState dict.
    """
    env_targets = os.getenv("MONITORING_TARGETS", "")
    if not targets:
        targets = [t.strip() for t in env_targets.split(",") if t.strip()] or DEFAULT_TARGETS

    _register_health_check_function()

    app = build_graph(scenario="monitoring")
    target_list = "\n".join(f"  - {t}" for t in targets)
    task = _TASK_TEMPLATE.format(target_list=target_list)

    initial_state = {
        "messages": [HumanMessage(content=task)],
        "plan": None,
        "iteration": 0,
        "error": None,
        "task_complete": False,
        "scenario": "monitoring",
    }
    result = app.invoke(initial_state)
    _print_result(result)
    return result


def _print_result(result: dict) -> None:
    """Print a summary of the workflow result to stdout."""
    print("\n" + "=" * 60)
    print("MONITORING AGENT — RESULT")
    print("=" * 60)
    last_msg = result["messages"][-1]
    print(last_msg.content)
    print(f"\nIterations: {result.get('iteration')} | Complete: {result.get('task_complete')}")


if __name__ == "__main__":
    extra_targets = sys.argv[1:] or None
    run(extra_targets)
