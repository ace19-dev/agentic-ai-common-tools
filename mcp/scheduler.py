"""
Scheduler MCP — background job scheduler with pluggable backends.

Backend is selected via the SCHEDULER_BACKEND environment variable:
  apscheduler (default) — in-process APScheduler + SQLite persistence
  celery                — distributed Celery + Redis/RabbitMQ broker

Security: callables must be pre-registered via register() before scheduling
to prevent LLMs from executing arbitrary code by name.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

import config
from core.base_mcp import BaseMCP, MCPResult
from mcp.backends.scheduler.base import BaseSchedulerBackend

logger = logging.getLogger(__name__)


class SchedulerMCP(BaseMCP):
    """Background job scheduler. Delegates all operations to a backend.

    The concrete backend is chosen at construction time via SCHEDULER_BACKEND env var.
    """

    def __init__(self, backend: BaseSchedulerBackend):
        self._backend = backend

    def register(self, name: str, func: Callable) -> None:
        """Pre-register a callable so the scheduler can execute it by name."""
        self._backend.register(name, func)

    def create(self, job_id: str, func_name: str,
               trigger: str, trigger_args: Dict,
               kwargs: Optional[Dict] = None) -> MCPResult:
        return self._backend.create(job_id, func_name, trigger, trigger_args, kwargs)

    def list_jobs(self) -> MCPResult:
        return self._backend.list_jobs()

    def remove(self, job_id: str) -> MCPResult:
        return self._backend.remove(job_id)

    def health_check(self) -> MCPResult:
        result = self._backend.health_check()
        if result.success and isinstance(result.data, dict):
            result.data["mcp"] = "scheduler"
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[SchedulerMCP] = None


def _create_backend() -> BaseSchedulerBackend:
    backend_type = config.SCHEDULER_BACKEND.lower()
    if backend_type == "celery":
        from mcp.backends.scheduler.celery import CeleryBackend
        if not config.SCHEDULER_CELERY_BROKER:
            raise ValueError("SCHEDULER_BACKEND=celery requires SCHEDULER_CELERY_BROKER to be set.")
        logger.info("Scheduler backend: Celery")
        return CeleryBackend(
            broker_url=config.SCHEDULER_CELERY_BROKER,
            backend_url=config.SCHEDULER_CELERY_RESULT_BACKEND or None,
        )
    logger.info("Scheduler backend: APScheduler")
    from mcp.backends.scheduler.apscheduler import APSchedulerBackend
    return APSchedulerBackend(db_path=config.SCHEDULER_DB_PATH)


def get_scheduler_mcp() -> SchedulerMCP:
    """Return the process-wide SchedulerMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = SchedulerMCP(_create_backend())
    return _instance
