"""All six MCP implementations and their singleton accessor functions."""
from .auth import AuthMCP, get_auth_mcp
from .http import HttpMCP, get_http_mcp
from .memory import MemoryMCP, get_memory_mcp
from .notification import NotificationMCP, get_notification_mcp
from .retrieval import RetrievalMCP, get_retrieval_mcp
from .scheduler import SchedulerMCP, get_scheduler_mcp

__all__ = [
    "MemoryMCP", "get_memory_mcp",
    "RetrievalMCP", "get_retrieval_mcp",
    "HttpMCP", "get_http_mcp",
    "SchedulerMCP", "get_scheduler_mcp",
    "NotificationMCP", "get_notification_mcp",
    "AuthMCP", "get_auth_mcp",
]
