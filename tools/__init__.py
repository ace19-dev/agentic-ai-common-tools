"""
All 20 domain tools exported as ALL_TOOLS for binding to the executor LLM.

Grouped by MCP: Memory (5), Retrieval (3), HTTP (2), Scheduler (3),
Notification (3), Auth (4).  Each tool is a LangChain @tool function backed
by the corresponding process-wide MCP singleton.
"""
from .auth_tools import auth_get_key, auth_revoke, auth_store_key, auth_validate
from .http_tools import http_get, http_post
from .memory_tools import memory_delete, memory_get, memory_list_keys, memory_search, memory_set
from .notification_tools import notify_console, notify_email, notify_slack
from .retrieval_tools import retrieval_delete, retrieval_index, retrieval_search
from .scheduler_tools import schedule_create, schedule_list, schedule_remove

ALL_TOOLS = [
    memory_get, memory_set, memory_delete, memory_list_keys, memory_search,
    retrieval_search, retrieval_index, retrieval_delete,
    http_get, http_post,
    schedule_create, schedule_list, schedule_remove,
    notify_email, notify_slack, notify_console,
    auth_store_key, auth_get_key, auth_validate, auth_revoke,
]

__all__ = [
    "ALL_TOOLS",
    "memory_get", "memory_set", "memory_delete", "memory_list_keys", "memory_search",
    "retrieval_search", "retrieval_index", "retrieval_delete",
    "http_get", "http_post",
    "schedule_create", "schedule_list", "schedule_remove",
    "notify_email", "notify_slack", "notify_console",
    "auth_store_key", "auth_get_key", "auth_validate", "auth_revoke",
]
