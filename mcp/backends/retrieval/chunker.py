from __future__ import annotations

import re
from typing import List


class TextChunker:
    """Split long text into overlapping chunks for RAG indexing.

    Splitting strategy (in priority order):
      1. Paragraph boundaries (double newline)
      2. Single newline / sentence boundaries
      3. Character-level fallback with overlap

    Args:
        chunk_size:    Target maximum character length per chunk.
        chunk_overlap: Number of characters to overlap between consecutive chunks.
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be in [0, chunk_size)")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> List[str]:
        """Return a list of non-empty text chunks."""
        text = text.strip()
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [text]

        # Try progressively finer separators
        for sep in ("\n\n", "\n", ". ", " "):
            chunks = self._split_by_sep(text, sep)
            if chunks:
                return chunks

        return self._char_split(text)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _split_by_sep(self, text: str, sep: str) -> List[str]:
        """Merge separator-based parts into chunks ≤ chunk_size."""
        parts = [p.strip() for p in text.split(sep) if p.strip()]
        if len(parts) <= 1:
            return []

        chunks: List[str] = []
        current = ""
        for part in parts:
            joined = (current + sep + part).strip() if current else part
            if len(joined) <= self.chunk_size:
                current = joined
            else:
                if current:
                    chunks.append(current)
                if len(part) > self.chunk_size:
                    chunks.extend(self._char_split(part))
                    current = ""
                else:
                    current = part
        if current:
            chunks.append(current)
        return chunks

    def _char_split(self, text: str) -> List[str]:
        """Hard character-level split with overlap as a last resort."""
        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start = end - self.chunk_overlap
        return chunks


def clean_html_text(html: str) -> str:
    """Extract readable text from an HTML string.

    Removes <script>, <style>, and navigation/header/footer tags, then
    collapses whitespace. Used by the crawl tools before chunking.
    """
    # Remove noisy tags wholesale
    html = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
                  " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(entity, char)
    # Collapse whitespace, preserve paragraph-like double newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
