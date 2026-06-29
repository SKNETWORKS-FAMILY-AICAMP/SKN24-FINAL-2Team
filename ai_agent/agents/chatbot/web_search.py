"""
agents/chatbot/web_search.py
DuckDuckGo fallback search — used only when a query has no card_data AND
policity_docs has nothing relevant, so the bot can still answer instead of
just asking a clarifying question.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from ddgs import DDGS

logger = logging.getLogger(__name__)


def _search_sync(query: str, max_results: int) -> List[Dict[str, Any]]:
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region="kr-kr"))


async def duckduckgo_search(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Return [{"title", "url", "snippet"}, ...] for the query, or [] on no
    results / failure. Runs the sync ddgs call in a thread so it doesn't block
    the event loop alongside the rest of this (async) codebase.
    """
    try:
        raw_results = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception:
        logger.exception("[web_search] duckduckgo_search failed for query=%r", query)
        return []

    results = [
        {
            "title":   r.get("title", ""),
            "url":     r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in raw_results
        if r.get("href")
    ]
    logger.info("[web_search] %d results for query=%r", len(results), query)
    return results
