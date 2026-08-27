"""Adaptive natural-language task parsing.

Turns a free-form instruction such as:

    "Search for the tallest mountain in Africa on Wikipedia and give the
     first 15 words of the introduction"

into a structured ParsedTask the rest of the system can act on, without
any hard-coded website selectors or fixed result positions. Parsing is
rule/regex based (no external LLM dependency required), designed to be
extended over time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from extractor import ExtractionInstruction

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
}

_ORDINAL_WORD = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "last": -1,
}


def _to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUM.get(token)


def _to_ordinal_index(token: str) -> int | None:
    """Convert an ordinal word/number ("second", "3rd") to a 0-based index."""
    token = token.strip().lower()
    match = re.match(r"^(\d+)(?:st|nd|rd|th)?$", token)
    if match:
        return int(match.group(1)) - 1
    if token in _ORDINAL_WORD:
        value = _ORDINAL_WORD[token]
        return value if value < 0 else value - 1
    return None


def _find_number_before(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return _to_int(match.group(1))


@dataclass
class VideoHints:
    """YouTube-specific hints extracted from the task text."""

    title_hint: str | None = None
    channel_hint: str | None = None
    timestamp: str | None = None
    duration_seconds: int | None = None
    info_request: str | None = None  # e.g. "view count", "upload date", "description"


@dataclass
class ParsedTask:
    raw_task: str
    task_type: str = "web_search"  # web_search | direct_url | youtube
    search_query: str | None = None
    target_url: str | None = None
    target_site: str | None = None  # domain hint, e.g. "wikipedia.org"
    section_hint: str | None = None
    extraction: ExtractionInstruction = field(default_factory=lambda: ExtractionInstruction(kind="raw"))
    verify: bool = True
    video: VideoHints | None = None
    multi_page: bool = False


_URL_RE = re.compile(r"(?:https?|file)://[^\s\"']+")
_SITE_RE = re.compile(
    r"\bon\s+([a-zA-Z0-9][a-zA-Z0-9\-]*(?:\.[a-zA-Z0-9\-]+)+|[A-Z][a-zA-Z0-9]+)\b"
)


class TaskDetector:
    """Rule-based parser for natural-language browsing/extraction tasks."""

    def parse(self, raw_task: str) -> ParsedTask:
        text = raw_task.strip()
        task = ParsedTask(raw_task=raw_task)

        self._detect_direct_url(text, task)
        is_youtube = self._detect_youtube(text, task)
        if not is_youtube and task.target_url is None:
            self._detect_target_site(text, task)
        self._detect_search_query(text, task)
        self._detect_section_hint(text, task)
        task.extraction = self._detect_extraction(text)
        task.verify = not bool(re.search(r"\bwithout verif\w*|no verif\w*", text, re.IGNORECASE))
        task.multi_page = bool(
            re.search(r"\bnext page|multiple pages|across pages|paginat\w*|second page|following page\b", text, re.IGNORECASE)
        )
        return task

    # -- individual detectors -------------------------------------------------

    def _detect_direct_url(self, text: str, task: ParsedTask) -> None:
        match = _URL_RE.search(text)
        if match:
            task.target_url = match.group(0).rstrip(".,);")
            task.task_type = "direct_url"

    def _detect_youtube(self, text: str, task: ParsedTask) -> bool:
        if not re.search(r"\byoutube\b|\bvideo\b", text, re.IGNORECASE):
            return False
        task.task_type = "youtube"
        task.target_site = "youtube.com"
        video = VideoHints()

        title_match = re.search(
            r"video\s+(?:titled|called|named)\s+[\"']([^\"']+)[\"']", text, re.IGNORECASE
        )
        if title_match:
            video.title_hint = title_match.group(1).strip()

        channel_match = re.search(
            r"(?:channel|by|from)\s+[\"']([^\"']+)[\"']", text, re.IGNORECASE
        ) or re.search(
            r"(?:channel|by|from)\s+([A-Za-z0-9][\w .\-]{1,40}?)(?:\s+on\s+youtube|\s+and\b|[.,]|$)",
            text,
            re.IGNORECASE,
        )
        if channel_match:
            video.channel_hint = channel_match.group(1).strip()

        timestamp_match = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", text)
        if timestamp_match:
            video.timestamp = timestamp_match.group(1)

        duration_match = re.search(
            r"(\d+)\s*(second|minute|hour)s?\s*(?:long|duration|mark|in)?", text, re.IGNORECASE
        )
        if duration_match:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2).lower()
            multiplier = {"second": 1, "minute": 60, "hour": 3600}[unit]
            video.duration_seconds = amount * multiplier

        for key, label in (
            ("view count", "view_count"), ("upload date", "upload_date"),
            ("description", "description"), ("title", "title"),
            ("channel name", "channel_name"), ("like count", "like_count"),
        ):
            if key in text.lower():
                video.info_request = label
                break

        task.video = video
        return True

    def _detect_target_site(self, text: str, task: ParsedTask) -> None:
        match = _SITE_RE.search(text)
        if match:
            site = match.group(1)
            task.target_site = site.lower()

    def _detect_search_query(self, text: str, task: ParsedTask) -> None:
        if task.task_type == "direct_url":
            return
        patterns = [
            r"search(?:\s+for)?\s+[\"']([^\"']+)[\"']",
            r"search(?:\s+for)?\s+(.+?)(?:\s+on\s+\S.*)?$",
            r"find\s+[\"']([^\"']+)[\"']",
            r"find\s+(.+?)(?:\s+on\s+\S.*)?$",
            r"look\s+up\s+(.+?)(?:\s+on\s+\S.*)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                query = match.group(1).strip().strip("\"'")
                query = re.sub(r"\s+and\s+(give|extract|return|provide).*$", "", query, flags=re.IGNORECASE)
                if query:
                    task.search_query = query
                    return

    def _detect_section_hint(self, text: str, task: ParsedTask) -> None:
        heading_match = re.search(
            r"(?:heading|section)\s+(?:called|named|titled)?\s*[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if heading_match:
            task.section_hint = heading_match.group(1).strip()

    def _detect_extraction(self, text: str) -> ExtractionInstruction:
        n = _find_number_before(r"first\s+(\w+)\s+words", text)
        if n is not None:
            return ExtractionInstruction(kind="first_n_words", n=n)

        n = _find_number_before(r"last\s+(\w+)\s+words", text)
        if n is not None:
            return ExtractionInstruction(kind="last_n_words", n=n)

        between_match = re.search(
            r"between\s+[\"']([^\"']+)[\"']\s+and\s+[\"']([^\"']+)[\"']", text, re.IGNORECASE
        ) or re.search(
            r"between\s+([^\"'.,]+?)\s+and\s+([^\"'.,]+?)(?:\s+and\b|[.,]|$)",
            text,
            re.IGNORECASE,
        )
        if between_match:
            return ExtractionInstruction(
                kind="between",
                start_marker=between_match.group(1).strip(),
                end_marker=between_match.group(2).strip(),
            )

        before_match = re.search(r"(?:text\s+)?before\s+[\"']?([^\"'.,]+)[\"']?", text, re.IGNORECASE)
        if before_match:
            return ExtractionInstruction(kind="before", marker=before_match.group(1).strip())

        after_match = re.search(r"(?:text\s+)?after\s+[\"']?([^\"'.,]+)[\"']?", text, re.IGNORECASE)
        if after_match:
            return ExtractionInstruction(kind="after", marker=after_match.group(1).strip())

        heading_match = re.search(
            r"(?:text|content|paragraph)\s+under\s+(?:the\s+)?heading\s+[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if heading_match:
            return ExtractionInstruction(kind="heading", heading=heading_match.group(1).strip())

        table_match = re.search(
            r"(?:table\s+)?(?:cell|value)\s+(?:for|of)\s+[\"']?([^\"'.,]+?)[\"']?\s+(?:in|and)\s+(?:column\s+)?[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if table_match:
            return ExtractionInstruction(
                kind="table", row_key=table_match.group(1).strip(), column_key=table_match.group(2).strip()
            )
        if re.search(r"\btable\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="table")

        list_index_match = re.search(r"(\w+)\s+item\s+(?:in|of)\s+the\s+list", text, re.IGNORECASE)
        if list_index_match:
            return ExtractionInstruction(kind="list", item_index=_to_ordinal_index(list_index_match.group(1)))
        if re.search(r"\blist\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="list")

        paragraph_match = re.search(r"paragraph\s+about\s+[\"']?([^\"'.,]+)[\"']?", text, re.IGNORECASE)
        if paragraph_match:
            return ExtractionInstruction(kind="paragraph", marker=paragraph_match.group(1).strip())
        if re.search(r"\bparagraph\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="paragraph")

        attribute_match = re.search(
            r"(href|src|alt|value)\s+of\s+[\"']?([^\"'.,]+)[\"']?", text, re.IGNORECASE
        )
        if attribute_match:
            return ExtractionInstruction(
                kind="attribute", attribute=attribute_match.group(1).lower(), selector_hint=attribute_match.group(2).strip()
            )

        return ExtractionInstruction(kind="raw")


def parse_task(raw_task: str) -> ParsedTask:
    """Module-level convenience wrapper around TaskDetector."""
    return TaskDetector().parse(raw_task)
