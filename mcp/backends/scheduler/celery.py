from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, Optional

from core.base_mcp import MCPResult
from mcp.backends.scheduler.base import BaseSchedulerBackend

logger = logging.getLogger(__name__)


class CeleryBackend(BaseSchedulerBackend):
    """Distributed task scheduler using Celery + Celery Beat.

    One-time tasks (trigger="date") are sent via apply_async with a countdown.
    Periodic tasks (trigger="interval" or "cron") are added to Celery Beat's
    schedule and require `celery -A <app_module> beat` to be running.

    Job metadata is kept in-memory for the process lifetime. For durable
    periodic schedules that survive restarts, configure Celery Beat with a
    persistent scheduler (e.g. django-celery-beat or celery-redbeat).

    Requires:
        pip install celery>=5.3
        A running broker — Redis: pip install redis
                         — RabbitMQ: pip install amqp

    Args:
        broker_url:  Celery broker DSN, e.g. "redis://localhost:6379/0"
        app_name:    Celery application name (default: "agent_scheduler")
        backend_url: Optional result backend DSN for task result storage.
    """

    def __init__(
        self,
        broker_url: str,
        app_name: str = "agent_scheduler",
        backend_url: Optional[str] = None,
    ):
        try:
            from celery import Celery  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "CeleryBackend requires celery. "
                "Install with: pip install celery[redis]"
            ) from exc

        from celery import Celery
        kwargs = {"backend": backend_url} if backend_url else {}
        self._app = Celery(app_name, broker=broker_url, **kwargs)
        self._registry: Dict[str, Callable] = {}
        self._jobs: Dict[str, dict] = {}

    def register(self, name: str, func: Callable) -> None:
        """Register a callable and expose it as a named Celery task."""
        self._registry[name] = func
        self._app.task(name=name)(func)
        logger.debug("Registered Celery task: %s", name)

    def create(self, job_id: str, func_name: str,
               trigger: str, trigger_args: Dict,
               kwargs: Optional[Dict] = None) -> MCPResult:
        if func_name not in self._registry:
            return MCPResult.fail(
                f"Function '{func_name}' not registered. "
                f"Available: {list(self._registry.keys())}"
            )
        kwargs = kwargs or {}
        try:
            if trigger == "date":
                self._schedule_one_time(func_name, trigger_args, kwargs)
            elif trigger == "interval":
                self._schedule_interval(job_id, func_name, trigger_args, kwargs)
            elif trigger == "cron":
                self._schedule_cron(job_id, func_name, trigger_args, kwargs)
            else:
                return MCPResult.fail(f"Unknown trigger '{trigger}'. Use: date, interval, cron")

            self._jobs[job_id] = {
                "func_name": func_name,
                "trigger": trigger,
                "trigger_args": trigger_args,
                "created_at": time.time(),
            }
            return MCPResult.ok(data={"job_id": job_id, "status": "scheduled"})
        except Exception as exc:
            logger.error("celery.create failed: %s", exc)
            return MCPResult.fail(str(exc))

    def _schedule_one_time(self, func_name: str, trigger_args: Dict, kwargs: Dict) -> None:
        import datetime
        run_date_str = trigger_args.get("run_date", "")
        if run_date_str:
            run_dt = datetime.datetime.fromisoformat(run_date_str)
            countdown = max(0.0, (run_dt - datetime.datetime.now()).total_seconds())
        else:
            countdown = float(trigger_args.get("countdown", 0))
        self._app.send_task(func_name, kwargs=kwargs, countdown=countdown)

    def _schedule_interval(self, job_id: str, func_name: str,
                           trigger_args: Dict, kwargs: Dict) -> None:
        from celery.schedules import schedule as celery_schedule
        seconds = (
            trigger_args.get("seconds", 0)
            + trigger_args.get("minutes", 0) * 60
            + trigger_args.get("hours", 0) * 3600
        )
        self._app.conf.beat_schedule[job_id] = {
            "task": func_name,
            "schedule": celery_schedule(seconds),
            "kwargs": kwargs,
        }

    def _schedule_cron(self, job_id: str, func_name: str,
                       trigger_args: Dict, kwargs: Dict) -> None:
        from celery.schedules import crontab
        self._app.conf.beat_schedule[job_id] = {
            "task": func_name,
            "schedule": crontab(**trigger_args),
            "kwargs": kwargs,
        }

    def list_jobs(self) -> MCPResult:
        jobs = [
            {"id": jid, **info, "trigger_args": info["trigger_args"]}
            for jid, info in self._jobs.items()
        ]
        return MCPResult.ok(data=jobs)

    def remove(self, job_id: str) -> MCPResult:
        if job_id not in self._jobs:
            return MCPResult.fail(f"job '{job_id}' not found")
        del self._jobs[job_id]
        self._app.conf.beat_schedule.pop(job_id, None)
        return MCPResult.ok(data="removed")

    def health_check(self) -> MCPResult:
        try:
            self._app.control.ping(timeout=0.5)
            broker_status = "reachable"
        except Exception:
            broker_status = "unreachable"
        return MCPResult.ok(data={
            "backend": "celery",
            "broker": self._app.conf.broker_url,
            "broker_status": broker_status,
            "registered_functions": list(self._registry.keys()),
        })
