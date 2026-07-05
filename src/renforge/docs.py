"""Offline Ren'Py documentation: list, read and search the SDK's bundled docs.

The Ren'Py SDK ships its full HTML documentation under ``<sdk>/doc/``. This
module turns those pages into plain text and offers keyword search, so an agent
can answer Ren'Py questions without network access. Parsed pages are cached in
memory per doc directory.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .sdk import RenpySdk

_SKIP_FILES = {"genindex.html", "search.html", "py-modindex.html"}
_cache: dict[str, dict[str, str]] = {}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title and self.title is None:
            self.title = data.strip()
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts))


def _doc_dir(sdk: RenpySdk) -> Path:
    return sdk.root / "doc"


def _load(sdk: RenpySdk) -> dict[str, str]:
    """Return ``{topic: plain_text}`` for all doc pages, cached per SDK."""
    doc_dir = _doc_dir(sdk)
    key = str(doc_dir)
    if key in _cache:
        return _cache[key]

    pages: dict[str, str] = {}
    if doc_dir.is_dir():
        for html in sorted(doc_dir.glob("*.html")):
            if html.name in _SKIP_FILES:
                continue
            try:
                raw = html.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parser = _TextExtractor()
            parser.feed(raw)
            pages[html.stem] = parser.text()
    _cache[key] = pages
    return pages


def list_docs(sdk: RenpySdk) -> list[str]:
    """List available documentation topics (page names without ``.html``)."""
    return sorted(_load(sdk).keys())


def get_doc(sdk: RenpySdk, topic: str, *, max_chars: int = 20000) -> dict[str, Any]:
    """Return the plain-text content of a documentation topic."""
    pages = _load(sdk)
    text = pages.get(topic)
    if text is None:
        # Tolerate "topic.html" or case differences.
        stem = topic[:-5] if topic.endswith(".html") else topic
        text = pages.get(stem)
    if text is None:
        return {"ok": False, "error": f"unknown doc topic: {topic}", "available_sample": list_docs(sdk)[:20]}
    return {"ok": True, "topic": topic, "text": text[:max_chars], "truncated": len(text) > max_chars}


def search_docs(sdk: RenpySdk, query: str, *, max_results: int = 8, snippet_chars: int = 240) -> dict[str, Any]:
    """Keyword-search the docs; return matching topics ranked by hit count."""
    pages = _load(sdk)
    needle = query.strip().lower()
    if not needle:
        return {"ok": False, "error": "empty query"}

    matches: list[dict[str, Any]] = []
    for topic, text in pages.items():
        lower = text.lower()
        count = lower.count(needle)
        if not count:
            continue
        pos = lower.find(needle)
        start = max(0, pos - snippet_chars // 2)
        snippet = text[start : start + snippet_chars].replace("\n", " ").strip()
        matches.append({"topic": topic, "hits": count, "snippet": snippet})

    matches.sort(key=lambda m: m["hits"], reverse=True)
    return {"ok": True, "query": query, "count": len(matches), "results": matches[:max_results]}


__all__ = ["list_docs", "get_doc", "search_docs"]
