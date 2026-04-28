"""
LangChain @tool wrappers for the HTTP MCP.

headers and params are JSON strings (not dicts) to avoid JSON Schema issues
when the LLM serialises tool arguments — some providers reject nested objects
in tool parameters, but plain string parameters always work.
"""
import json
from langchain_core.tools import tool
from mcp.http import get_http_mcp

_mcp = get_http_mcp()


def _parse_json_arg(raw: str, name: str) -> dict:
    """Safely parse a JSON string arg; returns {} on empty or malformed input."""
    if not raw or raw == "{}":
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


@tool
def http_get(url: str, headers: str = "{}", params: str = "{}") -> str:
    """Fetch content from a URL using an HTTP GET request.

    Automatically retries on transient server errors (429, 5xx) with exponential
    backoff. Response body is truncated to 10,000 characters.

    Args:
        url: Full URL including scheme, e.g. 'https://api.example.com/data'.
        headers: JSON string of HTTP request headers.
                 Example: '{"Authorization": "Bearer sk-...", "Accept": "application/json"}'
        params: JSON string of URL query parameters.
                Example: '{"page": "1", "limit": "20"}'

    Returns:
        JSON string with keys:
          - 'status_code' (int): HTTP status code
          - 'body' (str): response body (max 10,000 chars)
          - 'ok' (bool): true if status_code < 400
          - 'headers' (dict): response headers
        Returns 'ERROR: ...' on network failure.
    """
    return _mcp.get(
        url,
        headers=_parse_json_arg(headers, "headers") or None,
        params=_parse_json_arg(params, "params") or None,
    ).to_tool_str()


@tool
def http_post(url: str, json_body: str = "{}", headers: str = "{}") -> str:
    """Send an HTTP POST request with a JSON body.

    Automatically retries on transient server errors (429, 5xx).

    Args:
        url: Target URL including scheme.
        json_body: JSON string to send as the request body.
                   Example: '{"name": "Alice", "action": "subscribe"}'
        headers: JSON string of HTTP request headers.
                 Example: '{"Content-Type": "application/json"}'

    Returns:
        JSON string with 'status_code', 'body', 'ok', 'headers'.
        Returns 'ERROR: ...' on network failure.
    """
    return _mcp.post(
        url,
        json_body=_parse_json_arg(json_body, "json_body") or None,
        headers=_parse_json_arg(headers, "headers") or None,
    ).to_tool_str()
