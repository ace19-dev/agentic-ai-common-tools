from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MCPResult:
    """Standardized result envelope returned by every MCP operation.

    Attributes:
        success:  True if the operation completed without error.
        data:     The return value on success; None on failure.
        error:    Human-readable error message; None on success.
        metadata: Ancillary key-value info (e.g. row_count, elapsed_ms).
    """

    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    # ── Convenience constructors ───────────────────────────────────────────────

    @classmethod
    def ok(cls, data: Any = None, **meta) -> "MCPResult":
        return cls(success=True, data=data, metadata=meta)

    @classmethod
    def fail(cls, error: str, **meta) -> "MCPResult":
        return cls(success=False, error=error, metadata=meta)

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_tool_str(self) -> str:
        """Compact string suitable for returning from a LangChain @tool function."""
        if not self.success:
            return f"ERROR: {self.error}"
        if self.data is None:
            return "ok"
        if isinstance(self.data, (dict, list)):
            return json.dumps(self.data, ensure_ascii=False, default=str)
        return str(self.data)


class BaseMCP(ABC):
    """Abstract base class for all Model Context Protocol implementations.

    Every MCP wraps one or more backend services (databases, APIs, queues).
    Subclasses must implement `health_check` so monitoring agents can verify
    each backend is reachable without knowing implementation details.
    """

    @abstractmethod
    def health_check(self) -> MCPResult:
        """Return MCPResult.ok() if the backend is reachable, MCPResult.fail() otherwise."""
        ...
