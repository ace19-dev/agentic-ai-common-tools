from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from core.base_mcp import MCPResult


class BaseMemoryBackend(ABC):
    """Abstract interface that every memory backend must implement.

    Concrete implementation:
      SQLiteMemoryBackend — default, zero-config, file-backed
    """

    @abstractmethod
    def set(self, key: str, value: Any,
            namespace: str = "default",
            ttl: Optional[int] = None) -> MCPResult: ...

    @abstractmethod
    def get(self, key: str, namespace: str = "default") -> MCPResult: ...

    @abstractmethod
    def delete(self, key: str, namespace: str = "default") -> MCPResult: ...

    @abstractmethod
    def list_keys(self, namespace: str = "default") -> MCPResult: ...

    @abstractmethod
    def health_check(self) -> MCPResult: ...
