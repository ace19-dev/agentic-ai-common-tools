from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Callable, Dict, Optional

from core.base_mcp import MCPResult
from mcp.backends.scheduler.base import BaseSchedulerBackend

logger = logging.getLogger(__name__)


class APSchedulerBackend(BaseSchedulerBackend):
    """In-process scheduler using APScheduler with SQLite persistence.

    Jobs are persisted to SQLite and re-registered with APScheduler on startup.
    Callable whitelist prevents agents from scheduling arbitrary code.
    Falls back to persistence-only mode when APScheduler is unavailable.

    Requires: pip install APScheduler>=3.10
    """

    def __init__(self, db_path: str = "data/scheduler.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._registry: Dict[str, Callable] = {}
        self._scheduler = None
        self._init_db()
        self._start_scheduler()

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

    def _start_scheduler(self) -> None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._scheduler = BackgroundScheduler()
            self._scheduler.start()
            self._restore_jobs()
            logger.info("APScheduler started")
        except ImportError:
            logger.warning("APScheduler not installed — jobs will be persisted only")

    def _restore_jobs(self) -> None:
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
                    logger.warning("Skipping unregistered function '%s' for job '%s'",
                                   func_name, row["id"])
                    continue
                self._add_to_apscheduler(
                    row["id"], self._registry[func_name],
                    row["trigger"], json.loads(row["trigger_args"]),
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
                func, trigger, id=job_id, replace_existing=True,
                kwargs=kwargs, **trigger_args,
            )
        except Exception as exc:
            logger.error("APScheduler add_job failed for '%s': %s", job_id, exc)

    def register(self, name: str, func: Callable) -> None:
        self._registry[name] = func
        logger.debug("Registered scheduler function: %s", name)

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
                """, (job_id, func_name, trigger, json.dumps(trigger_args),
                      json.dumps(kwargs), time.time()))
                conn.commit()
            self._add_to_apscheduler(
                job_id, self._registry[func_name], trigger, trigger_args, kwargs
            )
            return MCPResult.ok(data={"job_id": job_id, "status": "scheduled"})
        except Exception as exc:
            logger.error("apscheduler.create failed: %s", exc)
            return MCPResult.fail(str(exc))

    def list_jobs(self) -> MCPResult:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, func_name, trigger, trigger_args FROM jobs WHERE status='active'"
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
            logger.error("apscheduler.list_jobs failed: %s", exc)
            return MCPResult.fail(str(exc))

    def remove(self, job_id: str) -> MCPResult:
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
            logger.error("apscheduler.remove failed: %s", exc)
            return MCPResult.fail(str(exc))

    def health_check(self) -> MCPResult:
        return MCPResult.ok(data={
            "backend": "apscheduler",
            "running": self._scheduler is not None and self._scheduler.running,
            "registered_functions": list(self._registry.keys()),
        })
