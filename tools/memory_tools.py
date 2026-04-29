"""
LangChain @tool wrappers for the Memory MCP.

Bound to the process-wide MemoryMCP singleton at import time so all agents
share the same backend instance (SQLite).
"""
import json
from langchain_core.tools import tool
from mcp.memory import get_memory_mcp

_mcp = get_memory_mcp()


@tool
def memory_get(key: str, namespace: str = "default") -> str:
    """Retrieve a previously stored value from persistent memory.

    Use this to recall information saved during earlier steps or conversation turns.

    Args:
        key: The unique identifier for the stored value.
        namespace: Logical grouping that isolates keys from other namespaces.
                   Defaults to 'default'.

    Returns:
        The stored value as a string, or 'ERROR: ...' if the key does not exist
        or has expired.
    """
    return _mcp.get(key, namespace=namespace).to_tool_str()


@tool
def memory_set(key: str, value: str, namespace: str = "default", ttl: int = 0) -> str:
    """Store a key-value pair in persistent memory with optional time-to-live.

    Use this to save data that subsequent steps or agents will need to recall.
    Complex objects should be JSON-serialised before passing as value.

    Args:
        key: Unique identifier for the value.
        value: The string value to store. Use JSON for structured data.
        namespace: Logical grouping. Defaults to 'default'.
        ttl: Expiry in seconds. 0 means the value never expires.

    Returns:
        'stored' on success, or 'ERROR: ...' on failure.
    """
    return _mcp.set(key, value, namespace=namespace, ttl=ttl or None).to_tool_str()


@tool
def memory_delete(key: str, namespace: str = "default") -> str:
    """Delete a stored key from persistent memory.

    Args:
        key: The key to delete.
        namespace: The namespace containing the key. Defaults to 'default'.

    Returns:
        'deleted' on success, or 'ERROR: ...' if the key was not found.
    """
    return _mcp.delete(key, namespace=namespace).to_tool_str()


@tool
def memory_list_keys(namespace: str = "default") -> str:
    """List all non-expired keys stored in a memory namespace.

    Args:
        namespace: The namespace to inspect. Defaults to 'default'.

    Returns:
        JSON array of key name strings, e.g. '["user_name", "session_id"]'.
        Returns '[]' if the namespace is empty.
    """
    result = _mcp.list_keys(namespace=namespace)
    if result.success:
        return json.dumps(result.data)
    return result.to_tool_str()
