from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional

from core.base_mcp import MCPResult


class BaseSchedulerBackend(ABC):
    """Abstract interface that every scheduler backend must implement.

    Concrete implementations:
      APSchedulerBackend — in-process APScheduler + SQLite persistence (default)
      CeleryBackend      — distributed Celery + Redis/RabbitMQ broker
    """

    @abstractmethod
    def register(self, name: str, func: Callable) -> None:
        """Pre-register a callable by name so agents can schedule it safely."""
        ...

    @abstractmethod
    def create(self, job_id: str, func_name: str,
               trigger: str, trigger_args: Dict,
               kwargs: Optional[Dict] = None) -> MCPResult: ...

    @abstractmethod
    def list_jobs(self) -> MCPResult: ...

    @abstractmethod
    def remove(self, job_id: str) -> MCPResult: ...

    @abstractmethod
    def health_check(self) -> MCPResult: ...
