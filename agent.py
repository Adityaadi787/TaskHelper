"""Browser agent: generic search/navigation behaviors built on top of
BrowserManager. Contains no website-specific selectors; result relevance
is determined by scoring link text/URL against the task's query and
target-site hint, never by a fixed result position.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page

from browser_manager import BrowserManager
from extractor import PageContent

logger = logging.getLogger("taskhelper.agent")


@dataclass
class Candidate:
    url: str
    text: str
    score: float = 0.0


_STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "and", "for", "to", "is", "are",
    "give", "find", "search", "what", "who", "when", "where", "how",
}


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def score_candidate(candidate_text: str, candidate_url: str, query: str, target_site: str | None) -> float:
    """Score a search-result candidate by relevance, never by position."""
    query_kw = _keywords(query)
    if not query_kw:
        return 0.0
    text_kw = _keywords(candidate_text)
    overlap = len(query_kw & text_kw)
    score = overlap / len(query_kw)

    if target_site:
        host = urlparse(candidate_url).netloc.lower()
        site = target_site.lower().removeprefix("www.")
        if site in host:
            score += 1.0
    return score


class BrowserAgent:
    """Coordinates a single BrowserManager to perform generic browse/search
    workflows requested by the task engine."""

    def __init__(self, manager: BrowserManager):
        self.manager = manager

    async def perform_search(self, page: Page, query: str, search_url_template: str) -> None:
        url = search_url_template.format(query=_url_encode(query))
        await self.manager.goto_with_retry(page, url)

    async def collect_candidates(self, page: Page, query: str, target_site: str | None, limit: int = 10) -> list[Candidate]:
        """Gather and score result links from whatever page we're on.

        Works generically against any results page: it inspects every
        visible anchor with non-trivial text, so it is not tied to one
        search engine's markup.
        """
        anchors = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: [e.innerText, e.getAttribute('title'), e.getAttribute('aria-label')].filter(Boolean).join(' ').trim()}))",
        )
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for a in anchors:
            href = a.get("href") or ""
            text = (a.get("text") or "").strip()
            if not href or not text or len(text) < 3:
                continue
            if href.startswith("javascript:") or "#" == href.strip():
                continue
            if href in seen:
                continue
            seen.add(href)
            score = score_candidate(text, href, query, target_site)
            if score > 0:
                candidates.append(Candidate(url=href, text=text, score=score))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: max(1, limit)]

    async def open_candidate(self, page: Page, candidate: Candidate) -> None:
        await self.manager.goto_with_retry(page, candidate.url)

    async def get_content(self, page: Page) -> PageContent:
        return await self.manager.extract_page_content(page)


def _url_encode(text: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(text)
