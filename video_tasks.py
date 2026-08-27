"""YouTube-specific task handling.

Parses YouTube hints from a ParsedTask (title/channel/timestamp/duration),
drives a YouTube search generically (no fixed result position), identifies
the most relevant video using available page text (title, channel, badges,
description snippet), and extracts the specifically requested information.

Does not attempt to bypass age gates, sign-in walls, or any other access
control -- if a requested action cannot be completed because of such a
barrier, the limitation is reported rather than faked.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.async_api import Page

from agent import BrowserAgent, Candidate, score_candidate
from browser_manager import BrowserManager
from extractor import PageContent, normalize_whitespace
from task_detector import VideoHints

logger = logging.getLogger("taskhelper.video_tasks")

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"


class VideoTaskError(Exception):
    """Raised when a YouTube task cannot be completed, with the real reason."""


@dataclass
class VideoResult:
    url: str
    title: str
    channel: str | None
    info: str | None = None


class VideoTaskHandler:
    def __init__(self, manager: BrowserManager, agent: BrowserAgent):
        self.manager = manager
        self.agent = agent

    async def search_and_identify(self, page: Page, query: str, hints: VideoHints) -> VideoResult:
        await self.manager.goto_with_retry(page, YOUTUBE_SEARCH_URL.format(query=_encode(query)))

        candidates = await self._collect_video_candidates(page, query, hints)
        if not candidates:
            raise VideoTaskError(
                f"No YouTube video results found for query {query!r}. "
                "The page may require sign-in/consent, or the DOM structure changed."
            )
        best = candidates[0]
        await self.agent.open_candidate(page, best)
        content = await self.agent.get_content(page)
        title = self._extract_title(content) or best.text
        channel = self._extract_channel(content)
        return VideoResult(url=page.url, title=title, channel=channel)

    async def _collect_video_candidates(self, page: Page, query: str, hints: VideoHints) -> list[Candidate]:
        items = await page.eval_on_selector_all(
            "a[href*='/watch']",
            "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))",
        )
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for item in items:
            href = item.get("href") or ""
            text = (item.get("text") or "").strip()
            if not href or "/watch" not in href or href in seen or not text:
                continue
            seen.add(href)
            score = score_candidate(text, href, query, None)
            if hints.title_hint and hints.title_hint.lower() in text.lower():
                score += 2.0
            if hints.channel_hint and hints.channel_hint.lower() in text.lower():
                score += 0.5
            candidates.append(Candidate(url=href, text=text, score=score))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _extract_title(self, content: PageContent) -> str | None:
        soup = content.soup()
        for selector in ("h1", "meta[name='title']", "title"):
            tag = soup.select_one(selector)
            if tag:
                text = tag.get("content") if tag.name == "meta" else tag.get_text(strip=True)
                if text:
                    return normalize_whitespace(text)
        return None

    def _extract_channel(self, content: PageContent) -> str | None:
        soup = content.soup()
        for selector in ("link[itemprop='name']", "span[itemprop='author'] link[itemprop='name']"):
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                return tag.get("content")
        author_tag = soup.find(attrs={"itemprop": "author"})
        if author_tag:
            name_tag = author_tag.find(attrs={"itemprop": "name"})
            if name_tag and name_tag.get("content"):
                return name_tag.get("content")
        return None

    def extract_requested_info(self, content: PageContent, hints: VideoHints) -> str:
        """Extract whatever specific piece of info was requested (view
        count, upload date, description, etc.) from the loaded video page,
        or report that it isn't available rather than guessing."""
        soup = content.soup()
        text = content.text()

        if hints.info_request == "view_count":
            match = re.search(r"([\d,\.]+[KMB]?)\s+views?", text, re.IGNORECASE)
            if match:
                return match.group(1)
            meta = soup.find("meta", itemprop="interactionCount")
            if meta and meta.get("content"):
                return meta["content"]
            raise VideoTaskError("View count not found on the loaded page.")

        if hints.info_request == "upload_date":
            meta = soup.find("meta", itemprop="uploadDate") or soup.find("meta", itemprop="datePublished")
            if meta and meta.get("content"):
                return meta["content"]
            raise VideoTaskError("Upload date not found on the loaded page.")

        if hints.info_request == "description":
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                return meta["content"]
            raise VideoTaskError("Description not found on the loaded page.")

        if hints.info_request in ("title", "channel_name"):
            title = self._extract_title(content)
            channel = self._extract_channel(content)
            value = title if hints.info_request == "title" else channel
            if value:
                return value
            raise VideoTaskError(f"{hints.info_request} not found on the loaded page.")

        if hints.timestamp:
            raise VideoTaskError(
                "Seeking to a specific timestamp and reading on-screen/transcript "
                "content at that instant is not supported without a transcript "
                "endpoint; only text already present in the page DOM can be read."
            )

        return normalize_whitespace(text[:2000])


def _encode(text: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(text)
