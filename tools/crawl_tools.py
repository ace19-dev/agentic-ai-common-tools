"""
LangChain @tool wrappers for web crawling + RAG indexing.

These tools fetch web content, clean HTML, chunk text, and index the chunks
into the Retrieval MCP in one step — the primary data-ingestion pipeline for RAG.

HTML parsing strategy:
  1. Use BeautifulSoup (pip install beautifulsoup4) when available — best quality.
  2. Fall back to regex-based stripping when BeautifulSoup is not installed.

Robots.txt: tools respect a configurable delay between requests and skip URLs
that return non-2xx status codes.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from langchain_core.tools import tool

from mcp.backends.retrieval.chunker import TextChunker, clean_html_text
from mcp.retrieval import get_retrieval_mcp

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": "AgenticAI-RAG-Crawler/1.0 (+https://github.com/ace19-dev/agentic-ai-common-tools)"
}
_DEFAULT_TIMEOUT = 15
_DEFAULT_CHUNK_SIZE = 500
_DEFAULT_CHUNK_OVERLAP = 50

_mcp = get_retrieval_mcp()


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _fetch_text(url: str, css_selector: str = "") -> tuple[str, int]:
    """Fetch URL and return (clean_text, status_code).

    Uses BeautifulSoup when available for best-quality text extraction.
    Falls back to regex stripping via clean_html_text().
    """
    resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_DEFAULT_TIMEOUT)
    raw = resp.text

    if css_selector:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "html.parser")
            el = soup.select_one(css_selector)
            text = el.get_text(separator="\n") if el else clean_html_text(raw)
        except ImportError:
            text = clean_html_text(raw)
    else:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
        except ImportError:
            text = clean_html_text(raw)

    # Collapse excess whitespace
    import re
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, resp.status_code


def _index_text(doc_id: str, text: str, meta: dict,
                chunk_size: int, chunk_overlap: int) -> str:
    """Chunk text and index all chunks. Returns a human-readable result string."""
    chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = chunker.split(text)
    if not chunks:
        return f"SKIPPED '{doc_id}': no content after cleaning"

    errors = []
    for i, chunk in enumerate(chunks):
        chunk_meta = {**meta, "_source_id": doc_id, "_chunk_index": i, "_total_chunks": len(chunks)}
        result = _mcp.index(f"{doc_id}__chunk_{i}", chunk, metadata=chunk_meta)
        if not result.success:
            errors.append(f"chunk {i}: {result.error}")

    if errors:
        return f"ERROR '{doc_id}': {len(errors)}/{len(chunks)} chunks failed — {errors[0]}"
    return f"indexed '{doc_id}': {len(chunks)} chunks"


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def crawl_and_index(
    url: str,
    doc_id: str = "",
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    metadata: str = "{}",
    css_selector: str = "",
) -> str:
    """Fetch a web page, extract its text, chunk it, and index it for RAG retrieval.

    This is the primary single-URL ingestion tool. Use crawl_and_index_urls for
    batch ingestion, or crawl_sitemap to crawl an entire site.

    Args:
        url:          URL to fetch and index.
        doc_id:       Document identifier. Defaults to the URL when empty.
        chunk_size:   Characters per chunk (default: 500). 0 = index as single doc.
        chunk_overlap: Character overlap between chunks (default: 50).
        metadata:     JSON string of extra metadata, e.g. '{"category": "docs"}'.
        css_selector: Optional CSS selector to extract specific content
                      (e.g. "main", "article", "#content"). Requires beautifulsoup4.

    Returns:
        "indexed '<doc_id>': N chunks" on success, or "ERROR: ..." on failure.
    """
    doc_id = doc_id.strip() or url
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}
    meta.setdefault("source_url", url)

    try:
        text, status = _fetch_text(url, css_selector)
    except Exception as exc:
        return f"ERROR fetching '{url}': {exc}"

    if status >= 400:
        return f"SKIPPED '{url}': HTTP {status}"
    if not text:
        return f"SKIPPED '{url}': empty content"

    # Delete stale chunks from a previous crawl of the same doc_id
    _mcp.delete_chunks(doc_id)

    return _index_text(doc_id, text, meta, chunk_size, chunk_overlap)


@tool
def crawl_and_index_urls(
    urls_json: str,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    metadata: str = "{}",
    css_selector: str = "",
    request_delay: float = 1.0,
) -> str:
    """Fetch multiple URLs and index them all for RAG retrieval.

    Each URL is used as its own doc_id. Stale chunks from previous crawls
    of the same URLs are automatically removed before re-indexing.

    Args:
        urls_json:     JSON array of URLs, e.g. '["https://a.com", "https://b.com"]'.
        chunk_size:    Characters per chunk (default: 500).
        chunk_overlap: Character overlap between chunks (default: 50).
        metadata:      JSON metadata applied to all pages (e.g. '{"source": "docs"}').
        css_selector:  Optional CSS selector for content extraction.
        request_delay: Seconds to sleep between requests (default: 1.0).

    Returns:
        Summary: "Crawled N URLs: X indexed, Y skipped, Z errors."
        Followed by per-URL results.
    """
    try:
        urls = json.loads(urls_json)
        if not isinstance(urls, list):
            return "ERROR: urls_json must be a JSON array of strings"
    except json.JSONDecodeError as exc:
        return f"ERROR: invalid urls_json — {exc}"

    try:
        base_meta = json.loads(metadata)
    except json.JSONDecodeError:
        base_meta = {}

    results = []
    indexed = skipped = errors = 0
    for i, url in enumerate(urls):
        if i > 0 and request_delay > 0:
            time.sleep(request_delay)

        try:
            text, status = _fetch_text(url, css_selector)
        except Exception as exc:
            results.append(f"ERROR '{url}': {exc}")
            errors += 1
            continue

        if status >= 400:
            results.append(f"SKIPPED '{url}': HTTP {status}")
            skipped += 1
            continue
        if not text:
            results.append(f"SKIPPED '{url}': empty content")
            skipped += 1
            continue

        meta = {**base_meta, "source_url": url}
        _mcp.delete_chunks(url)
        msg = _index_text(url, text, meta, chunk_size, chunk_overlap)
        results.append(msg)
        if msg.startswith("ERROR"):
            errors += 1
        else:
            indexed += 1

    summary = f"Crawled {len(urls)} URLs: {indexed} indexed, {skipped} skipped, {errors} errors."
    return summary + "\n" + "\n".join(results)


@tool
def crawl_sitemap(
    sitemap_url: str,
    max_pages: int = 50,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    metadata: str = "{}",
    css_selector: str = "",
    request_delay: float = 1.0,
) -> str:
    """Fetch a sitemap.xml, crawl all listed URLs, and index them for RAG.

    Supports standard sitemap format (<urlset><url><loc>...). For sitemap
    index files (<sitemapindex>), only the first-level sitemaps are expanded.

    Args:
        sitemap_url:   URL of the sitemap.xml file.
        max_pages:     Maximum number of pages to crawl (default: 50).
        chunk_size:    Characters per chunk (default: 500).
        chunk_overlap: Character overlap between chunks (default: 50).
        metadata:      JSON metadata applied to all pages.
        css_selector:  Optional CSS selector for content extraction.
        request_delay: Seconds between page requests (default: 1.0).

    Returns:
        Summary with per-URL results.
    """
    import re

    try:
        base_meta = json.loads(metadata)
    except json.JSONDecodeError:
        base_meta = {}

    # Fetch sitemap
    try:
        resp = requests.get(sitemap_url, headers=_DEFAULT_HEADERS, timeout=_DEFAULT_TIMEOUT)
        resp.raise_for_status()
        sitemap_xml = resp.text
    except Exception as exc:
        return f"ERROR fetching sitemap '{sitemap_url}': {exc}"

    # Extract <loc> URLs — handles both urlset and sitemapindex
    locs = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", sitemap_xml)
    if not locs:
        return f"ERROR: no <loc> entries found in '{sitemap_url}'"

    # If this is a sitemap index, expand sub-sitemaps (one level)
    is_index = "<sitemapindex" in sitemap_xml
    if is_index:
        page_locs: list[str] = []
        for sub_url in locs[:10]:  # limit sub-sitemap expansion
            try:
                sub_resp = requests.get(sub_url, headers=_DEFAULT_HEADERS, timeout=_DEFAULT_TIMEOUT)
                sub_locs = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", sub_resp.text)
                page_locs.extend(sub_locs)
                if len(page_locs) >= max_pages:
                    break
            except Exception:
                pass
        locs = page_locs

    locs = locs[:max_pages]
    urls_json = json.dumps(locs)
    return crawl_and_index_urls.invoke({
        "urls_json": urls_json,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "metadata": json.dumps({**base_meta, "sitemap": sitemap_url}),
        "css_selector": css_selector,
        "request_delay": request_delay,
    })


@tool
def crawl_recursive(
    start_url: str,
    max_pages: int = 20,
    same_domain_only: bool = True,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    metadata: str = "{}",
    css_selector: str = "",
    request_delay: float = 1.0,
) -> str:
    """Recursively crawl a website starting from a URL and index all pages for RAG.

    Follows internal links (href) up to max_pages. By default stays within
    the same domain as start_url to avoid crawling the entire internet.

    Requires: pip install beautifulsoup4

    Args:
        start_url:       URL to begin crawling from.
        max_pages:       Maximum pages to crawl (default: 20).
        same_domain_only: Only follow links on the same domain (default: True).
        chunk_size:      Characters per chunk (default: 500).
        chunk_overlap:   Character overlap between chunks (default: 50).
        metadata:        JSON metadata applied to all pages.
        css_selector:    Optional CSS selector for content extraction.
        request_delay:   Seconds between requests (default: 1.0).

    Returns:
        Summary with per-URL results.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ("ERROR: crawl_recursive requires beautifulsoup4. "
                "Install with: pip install beautifulsoup4")

    try:
        base_meta = json.loads(metadata)
    except json.JSONDecodeError:
        base_meta = {}

    start_domain = urlparse(start_url).netloc
    visited: set[str] = set()
    queue: list[str] = [start_url]
    results: list[str] = []
    indexed = skipped = errors = 0

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        if len(visited) > 1 and request_delay > 0:
            time.sleep(request_delay)

        try:
            resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=_DEFAULT_TIMEOUT)
            status = resp.status_code
            html = resp.text
        except Exception as exc:
            results.append(f"ERROR '{url}': {exc}")
            errors += 1
            continue

        if status >= 400:
            results.append(f"SKIPPED '{url}': HTTP {status}")
            skipped += 1
            continue

        # Extract links before cleaning HTML
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            parsed = urlparse(href)
            if parsed.scheme not in ("http", "https"):
                continue
            if same_domain_only and parsed.netloc != start_domain:
                continue
            if href not in visited and href not in queue:
                queue.append(href)

        # Extract text
        if css_selector:
            el = soup.select_one(css_selector)
            text = el.get_text(separator="\n") if el else ""
        else:
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            text = soup.get_text(separator="\n")

        import re
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            results.append(f"SKIPPED '{url}': empty content")
            skipped += 1
            continue

        meta = {**base_meta, "source_url": url, "crawl_start": start_url}
        _mcp.delete_chunks(url)
        msg = _index_text(url, text, meta, chunk_size, chunk_overlap)
        results.append(msg)
        if msg.startswith("ERROR"):
            errors += 1
        else:
            indexed += 1

    total = len(visited)
    summary = f"Crawled {total} pages from '{start_url}': {indexed} indexed, {skipped} skipped, {errors} errors."
    return summary + "\n" + "\n".join(results)
