"""
HTTP MCP — resilient HTTP client with automatic retry and response truncation.

Uses a requests.Session with a urllib3 Retry adapter so transient server
errors (429, 5xx) are retried with exponential backoff without caller changes.
"""
import logging
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from core.base_mcp import BaseMCP, MCPResult

logger = logging.getLogger(__name__)

# LLM context windows are finite; truncate large HTML/JSON bodies to avoid
# wasting tokens and hitting the model's input limit.
_MAX_BODY = 10_000


class HttpMCP(BaseMCP):
    """Resilient HTTP client with configurable retry, backoff, and timeout.

    Retries automatically on transient server errors (429, 5xx) using
    exponential backoff. All exceptions are caught and returned as MCPResult.fail.
    Response bodies are truncated to _MAX_BODY characters.
    """

    def __init__(self,
                 timeout: int = 10,
                 max_retries: int = 3,
                 backoff_factor: float = 0.5):
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_result(self, resp: requests.Response) -> MCPResult:
        """Wrap an HTTP response into a normalised MCPResult dict."""
        return MCPResult.ok(data={
            "status_code": resp.status_code,
            "body": resp.text[:_MAX_BODY],
            "ok": resp.ok,
            "headers": dict(resp.headers),
        })

    def _handle_error(self, exc: Exception, url: str) -> MCPResult:
        """Convert a requests exception into a descriptive MCPResult.fail()."""
        if isinstance(exc, requests.exceptions.Timeout):
            return MCPResult.fail(f"Request timed out after {self.timeout}s: {url}")
        if isinstance(exc, requests.exceptions.ConnectionError):
            return MCPResult.fail(f"Connection error for {url}: {exc}")
        return MCPResult.fail(f"Request failed for {url}: {exc}")

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, url: str,
            headers: Optional[Dict] = None,
            params: Optional[Dict] = None) -> MCPResult:
        try:
            resp = self.session.get(
                url, headers=headers, params=params, timeout=self.timeout
            )
            return self._build_result(resp)
        except Exception as exc:
            logger.warning("http.get failed [%s]: %s", url, exc)
            return self._handle_error(exc, url)

    def post(self, url: str,
             json_body: Optional[Dict] = None,
             data: Optional[Dict] = None,
             headers: Optional[Dict] = None) -> MCPResult:
        try:
            resp = self.session.post(
                url, json=json_body, data=data, headers=headers, timeout=self.timeout
            )
            return self._build_result(resp)
        except Exception as exc:
            logger.warning("http.post failed [%s]: %s", url, exc)
            return self._handle_error(exc, url)

    def health_check(self) -> MCPResult:
        result = self.get("https://httpbin.org/get")
        if result.success and result.data.get("status_code") == 200:
            return MCPResult.ok(data={"mcp": "http", "connectivity": "ok"})
        error = result.error or f"unexpected status {result.data}"
        return MCPResult.fail(f"connectivity check failed: {error}")


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[HttpMCP] = None


def get_http_mcp() -> HttpMCP:
    """Return the process-wide HttpMCP singleton, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = HttpMCP(
            timeout=config.HTTP_TIMEOUT,
            max_retries=config.HTTP_MAX_RETRIES,
        )
    return _instance
