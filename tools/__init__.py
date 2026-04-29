"""
All domain tools exported for binding to LLM agents.

Grouped by MCP:
  Memory (4)       — get, set, delete, list_keys
  Retrieval (5)    — search, build_context, index, delete, delete_chunks
  Crawl (4)        — crawl_and_index, crawl_and_index_urls, crawl_sitemap, crawl_recursive
  HTTP (2)         — get, post
  Scheduler (3)    — create, list, remove
  Notification (3) — email, slack, console
  Auth (5)         — store_key, get_key, validate, list_services, revoke
  Flight (2)       — search, book  (requires configure_flight_client() at startup;
                     NOT included in ALL_TOOLS — add per-agent as needed)
"""
from .auth_tools import (auth_get_key, auth_list_services, auth_revoke,
                         auth_store_key, auth_validate)
from .crawl_tools import (crawl_and_index, crawl_and_index_urls, crawl_recursive,
                          crawl_sitemap)
from .flight_tools import flight_book, flight_search
from .http_tools import http_get, http_post
from .memory_tools import memory_delete, memory_get, memory_list_keys, memory_set
from .notification_tools import notify_console, notify_email, notify_slack
from .retrieval_tools import (retrieval_build_context, retrieval_delete,
                               retrieval_delete_chunks, retrieval_index, retrieval_search)
from .scheduler_tools import schedule_create, schedule_list, schedule_remove

ALL_TOOLS = [
    # Memory
    memory_get, memory_set, memory_delete, memory_list_keys,
    # Retrieval + RAG
    retrieval_search, retrieval_build_context,
    retrieval_index, retrieval_delete, retrieval_delete_chunks,
    # Crawl (RAG ingestion)
    crawl_and_index, crawl_and_index_urls, crawl_sitemap, crawl_recursive,
    # HTTP
    http_get, http_post,
    # Scheduler
    schedule_create, schedule_list, schedule_remove,
    # Notification
    notify_email, notify_slack, notify_console,
    # Auth
    auth_store_key, auth_get_key, auth_validate, auth_list_services, auth_revoke,
    # Note: flight_search / flight_book are NOT here — they require configure_flight_client()
]

__all__ = [
    "ALL_TOOLS",
    "memory_get", "memory_set", "memory_delete", "memory_list_keys",
    "retrieval_search", "retrieval_build_context",
    "retrieval_index", "retrieval_delete", "retrieval_delete_chunks",
    "crawl_and_index", "crawl_and_index_urls", "crawl_sitemap", "crawl_recursive",
    "http_get", "http_post",
    "schedule_create", "schedule_list", "schedule_remove",
    "notify_email", "notify_slack", "notify_console",
    "auth_store_key", "auth_get_key", "auth_validate", "auth_list_services", "auth_revoke",
    "flight_search", "flight_book",
]
