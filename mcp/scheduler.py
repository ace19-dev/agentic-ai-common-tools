"""
Scheduler MCP — background job scheduler with SQLite persistence.

Uses APScheduler's BackgroundScheduler (if installed) for actual execution.
All jobs are also written to SQLite so they survive process restarts and can
be inspected without APScheduler.

Security design: callables must be pre-registered via register() before
scheduling.  This whitelist prevents an LLM from scheduling arbitrary
Python expressions by passing arbitrary func_name strings.
"""
import json
import logging
import os
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)


class SchedulerMCP(BaseMCP):
    """SQLite-backed job scheduler with APScheduler for background execution.

    Jobs are persisted to SQLite across restarts. Callables must be pre-registered
    via `register()` before scheduling to avoid arbitrary code execution.
    Falls back to persistence-only mode when APScheduler is unavailable.
    """

    def __init__(self, db_path: str = "data/scheduler.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._registry: Dict[str, Callable] = {}
        self._scheduler = None
        self._init_db()
        self._start_scheduler()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id           TEXT PRIMARY KEY,
                    func_name    TEXT NOT NULL,
                    trigger      TEXT NOT NULL,
                    trigger_args TEXT NOT NULL DEFAULT '{}',
                    kwargs       TEXT NOT NULL DEFAULT '{}',
                    status       TEXT NOT NULL DEFAULT 'active',
                    created_at   REAL NOT NULL
                )
            """)
            conn.commit()

    # ── APScheduler ──────────────────────────────────────────────────────────

    def _start_scheduler(self) -> None:
        """Start APScheduler; fall back to persistence-only mode if not installed."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._scheduler = BackgroundScheduler()
            self._scheduler.start()
            self._restore_jobs()
            logger.info("APScheduler started")
        except ImportError:
            logger.warning("APScheduler not installed — jobs will be persisted only")
            self._scheduler = None

    def _restore_jobs(self) -> None:
        """Re-register all 'active' SQLite jobs with APScheduler on startup."""
        if not self._scheduler:
            return
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status='active'"
                ).fetchall()
            for row in rows:
                func_name = row["func_name"]
                if func_name not in self._registry:
                    logger.warning(
                        "Skipping unregistered function '%s' for job '%s'",
                        func_name, row["id"],
                    )
                    continue
                self._add_to_apscheduler(
                    row["id"],
                    self._registry[func_name],
                    row["trigger"],
                    json.loads(row["trigger_args"]),
                    json.loads(row["kwargs"]),
                )
        except Exception as exc:
            logger.error("Failed to restore jobs: %s", exc)

    def _add_to_apscheduler(self, job_id: str, func: Callable,
                             trigger: str, trigger_args: Dict, kwargs: Dict) -> None:
        if not self._scheduler:
            return
        try:
            self._scheduler.add_job(
                func, trigger,
                id=job_id,
                replace_existing=True,
                kwargs=kwargs,
                **trigger_args,
            )
        except Exception as exc:
            logger.error("APScheduler add_job failed for '%s': %s", job_id, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, name: str, func: Callable) -> None:
        """Pre-register a callable so the scheduler can execute it by name."""
        self._registry[name] = func
        logger.debug("Registered scheduler function: %s", name)

    def create(self, job_id: str, func_name: str,
               trigger: str, trigger_args: Dict,
               kwargs: Optional[Dict] = None) -> MCPResult:
        """Persist and schedule a job; rejects unregistered function names."""
        if func_name not in self._registry:
            return MCPResult.fail(
                f"Function '{func_name}' not registered. "
                f"Available: {list(self._registry.keys())}"
            )
        kwargs = kwargs or {}
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO jobs (id, func_name, trigger, trigger_args, kwargs, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        func_name    = excluded.func_name,
                        trigger      = excluded.trigger,
                        trigger_args = excluded.trigger_args,
                        kwargs       = excluded.kwargs,
                        status       = 'active'
                """, (job_id, func_name, trigger, json.dumps(trigger_args), json.dumps(kwargs), time.time()))
                conn.commit()
            self._add_to_apscheduler(
                job_id, self._registry[func_name], trigger, trigger_args, kwargs
            )
            return MCPResult.ok(data={"job_id": job_id, "status": "scheduled"})
        except Exception as exc:
            logger.error("scheduler.create failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_jobs(self) -> MCPResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, func_name, trigger, trigger_args, status FROM jobs WHERE status='active'"
                ).fetchall()
            jobs = []
            for row in rows:
                entry = {
                    "id": row["id"],
                    "func_name": row["func_name"],
                    "trigger": row["trigger"],
                    "trigger_args": json.loads(row["trigger_args"]),
                }
                if self._scheduler:
                    apj = self._scheduler.get_job(row["id"])
                    entry["next_run_time"] = str(apj.next_run_time) if apj and apj.next_run_time else None
                jobs.append(entry)
            return MCPResult.ok(data=jobs)
        except Exception as exc:
            logger.error("scheduler.list_jobs failed: %s", exc)
            return MCPResult.fail(str(exc))

    def remove(self, job_id: str) -> MCPResult:
        """Cancel a job: marks it 'cancelled' in SQLite and removes it from APScheduler."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE jobs SET status='cancelled' WHERE id=? AND status='active'",
                    (job_id,),
                )
                conn.commit()
            if cursor.rowcount == 0:
                return MCPResult.fail(f"job '{job_id}' not found or already cancelled")
            if self._scheduler:
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:
                    pass
            return MCPResult.ok(data="removed")
        except Exception as exc:
            logger.error("scheduler.remove failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        return MCPResult.ok(data={
            "mcp": "scheduler",
            "apscheduler_running": self._scheduler is not None and self._scheduler.running,
            "registered_functions": list(self._registry.keys()),
        })


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[SchedulerMCP] = None


def get_scheduler_mcp() -> SchedulerMCP:
    """Return the process-wide SchedulerMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = SchedulerMCP(db_path=config.SCHEDULER_DB_PATH)
    return _instance
