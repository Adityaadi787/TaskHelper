"""Adaptive natural-language task parsing for web and YouTube workflows.

The parser is deliberately dependency-light and selector-free. It converts a
free-form instruction into structured hints consumed by the browser engine.
It does not hard-code a website, search query, video, timestamp, or result
position.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from extractor import ExtractionInstruction

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
}
_ORDINAL_WORD = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "sixth": 5, "seventh": 6, "eighth": 7, "ninth": 8, "tenth": 9, "last": -1,
}


def _to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUM.get(token)


def _to_ordinal_index(token: str) -> int | None:
    token = token.strip().lower()
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", token)
    if match:
        return int(match.group(1)) - 1
    return _ORDINAL_WORD.get(token)


def _duration_seconds(amount: int, unit: str) -> int:
    return amount * {"second": 1, "seconds": 1, "minute": 60, "minutes": 60,
                      "hour": 3600, "hours": 3600}[unit.lower()]


@dataclass
class VideoHints:
    title_hint: str | None = None
    channel_hint: str | None = None
    thumbnail_hint: str | None = None
    timestamp: str | None = None
    interval_start: str | None = None
    interval_end: str | None = None
    duration_seconds: int | None = None
    info_request: str | None = None
    reference_text: str | None = None


@dataclass
class ParsedTask:
    raw_task: str
    task_type: str = "web_search"
    search_query: str | None = None
    target_url: str | None = None
    target_site: str | None = None
    section_hint: str | None = None
    extraction: ExtractionInstruction = field(
        default_factory=lambda: ExtractionInstruction(kind="raw")
    )
    verify: bool = True
    video: VideoHints | None = None
    multi_page: bool = False


_URL_RE = re.compile(r"(?:https?|file)://[^\s\"']+")


class TaskDetector:
    """Parse a user-authorized task into a generic execution plan."""

    def parse(self, raw_task: str) -> ParsedTask:
        text = raw_task.strip()
        if not text:
            raise ValueError("Task instruction cannot be empty")

        task = ParsedTask(raw_task=raw_task)
        is_youtube = self._detect_youtube(text, task)

        if not is_youtube:
            self._detect_direct_url(text, task)
            if task.target_url is None:
                self._detect_target_site(text, task)

        self._detect_search_query(text, task)
        self._detect_section_hint(text, task)
        task.extraction = self._detect_extraction(text)
        task.verify = not bool(
            re.search(r"\b(?:without|no)\s+verif\w*", text, re.IGNORECASE)
        )
        task.multi_page = bool(
            re.search(
                r"\b(?:next page|multiple pages|across pages|all pages|"
                r"pagination|second page|following pages)\b",
                text,
                re.IGNORECASE,
            )
        )
        return task

    def _detect_direct_url(self, text: str, task: ParsedTask) -> None:
        match = _URL_RE.search(text)
        if match:
            task.target_url = match.group(0).rstrip(".,);]")
            task.task_type = "direct_url"

    def _detect_youtube(self, text: str, task: ParsedTask) -> bool:
        youtube_url = re.search(
            r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+",
            text,
            re.IGNORECASE,
        )
        if not re.search(r"\byoutube\b|\bvideo\b", text, re.IGNORECASE) and not youtube_url:
            return False

        task.task_type = "youtube"
        task.target_site = "youtube.com"
        video = VideoHints()

        for pattern in (
            r"video\s+(?:titled|called|named)\s+[\"']([^\"']+)[\"']",
            r"target\s+video\s*[:=]?\s*[\"']([^\"']+)[\"']",
            r"(?:video\s+)?title\s*[:=]\s*[\"']([^\"']+)[\"']",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                video.title_hint = match.group(1).strip()
                break

        for pattern in (
            r"(?:channel|creator|uploader|by|from)\s*[:=]?\s*[\"']([^\"']+)[\"']",
            r"(?:channel|creator|uploader)\s*[:=]\s*([^\n,.;]+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                video.channel_hint = match.group(1).strip()
                break

        match = re.search(
            r"thumbnail(?:\s+(?:says|shows|contains|text))?\s*[:=]?\s*[\"']([^\"']+)[\"']",
            text,
            re.IGNORECASE,
        )
        if match:
            video.thumbnail_hint = match.group(1).strip()

        times = re.findall(
            r"\b(?:[0-5]?\d:)?[0-5]?\d:[0-5]\d\b|\b[0-5]?\d:[0-5]\d\b",
            text,
        )
        if times:
            video.timestamp = times[0]

        match = re.search(
            r"(?:from|between)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+"
            r"(?:to|and)\s+(\d{1,2}:\d{2}(?::\d{2})?)",
            text,
            re.IGNORECASE,
        )
        if match:
            video.interval_start, video.interval_end = match.groups()

        match = re.search(
            r"(?:watch|view|play|for)\s+(\d+)\s*(seconds?|minutes?|hours?)\b",
            text,
            re.IGNORECASE,
        )
        if match:
            video.duration_seconds = _duration_seconds(
                int(match.group(1)), match.group(2)
            )

        if not video.duration_seconds:
            match = re.search(
                r"duration\s*[:=]?\s*(\d+):([0-5]\d)(?::([0-5]\d))?",
                text,
                re.IGNORECASE,
            )
            if match:
                h_or_min, seconds, hours_seconds = match.groups()
                if hours_seconds is not None:
                    video.duration_seconds = (
                        int(h_or_min) * 3600 + int(seconds) * 60 + int(hours_seconds)
                    )
                else:
                    video.duration_seconds = int(h_or_min) * 60 + int(seconds)

        requests = (
            ("view count", "view_count"),
            ("upload date", "upload_date"),
            ("description", "description"),
            ("like count", "like_count"),
            ("subscriber count", "subscriber_count"),
            ("channel name", "channel_name"),
            ("title", "title"),
        )
        low = text.lower()
        for label, key in requests:
            if label in low:
                video.info_request = key
                break

        match = re.search(
            r"(?:return|give|extract|copy)\s+(?:the\s+)?(?:exact\s+)?"
            r"(?:text|information|value)\s+(?:at|around|near)\s+"
            r"(\d{1,2}:\d{2}(?::\d{2})?)",
            text,
            re.IGNORECASE,
        )
        if match:
            video.timestamp = match.group(1)

        match = re.search(
            r"(?:reference|look for|find)\s+(?:text|phrase|wording)\s*"
            r"[=:]?\s*[\"']([^\"']+)[\"']",
            text,
            re.IGNORECASE,
        )
        if match:
            video.reference_text = match.group(1).strip()

        task.video = video
        return True

    def _detect_target_site(self, text: str, task: ParsedTask) -> None:
        match = re.search(
            r"\bon\s+([a-zA-Z0-9][a-zA-Z0-9.-]*(?:\.[a-zA-Z0-9-]+)+|Wikipedia)\b",
            text,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1)
            task.target_site = value.lower() if "." in value else "wikipedia"

    def _detect_search_query(self, text: str, task: ParsedTask) -> None:
        if task.task_type == "direct_url":
            return

        patterns = (
            r"(?:search(?:\s+for)?|find|look\s+up)\s*[:=]?\s*[\"']([^\"']+)[\"']",
            r"(?:search(?:\s+for)?|find|look\s+up)\s*[:=]?\s*(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            query = match.group(1).strip().strip("\"'")
            query = re.split(
                r"\s+(?:and\s+)?(?:give|extract|return|provide|tell|copy)\b",
                query,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ,.;")
            query = re.sub(
                r"\s+on\s+(?:wikipedia|youtube|[A-Za-z0-9.-]+)$",
                "",
                query,
                flags=re.IGNORECASE,
            ).strip()
            if query:
                task.search_query = query
                return

        if task.task_type == "youtube" and task.video and task.video.title_hint:
            task.search_query = task.video.title_hint

    def _detect_section_hint(self, text: str, task: ParsedTask) -> None:
        match = re.search(
            r"(?:heading|section)\s+(?:called|named|titled)?\s*[\"']?"
            r"([^\"'.,]+?)[\"']?(?:\s+(?:and|then)|[.,]|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            task.section_hint = match.group(1).strip()

    def _detect_extraction(self, text: str) -> ExtractionInstruction:
        for kind, prefix in (("first_n_words", "first"), ("last_n_words", "last")):
            match = re.search(rf"{prefix}\s+(\w+)\s+words", text, re.IGNORECASE)
            if match:
                n = _to_int(match.group(1))
                if n is not None:
                    return ExtractionInstruction(kind=kind, n=n)

        match = re.search(
            r"between\s+[\"']([^\"']+)[\"']\s+and\s+[\"']([^\"']+)[\"']",
            text,
            re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r"between\s+([^\"'.,]+?)\s+and\s+([^\"'.,]+?)"
                r"(?:\s+(?:and|then)\b|[.,]|$)",
                text,
                re.IGNORECASE,
            )
        if match:
            return ExtractionInstruction(
                kind="between",
                start_marker=match.group(1).strip(),
                end_marker=match.group(2).strip(),
            )

        for kind, word in (("before", "before"), ("after", "after")):
            match = re.search(
                rf"(?:text\s+)?{word}\s+[\"']?([^\"'.,]+)[\"']?",
                text,
                re.IGNORECASE,
            )
            if match:
                return ExtractionInstruction(kind=kind, marker=match.group(1).strip())

        match = re.search(
            r"(?:text|content|paragraph)\s+under\s+(?:the\s+)?heading\s+"
            r"[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if match:
            return ExtractionInstruction(kind="heading", heading=match.group(1).strip())

        match = re.search(
            r"(?:table\s+)?(?:cell|value)\s+(?:for|of)\s+[\"']?([^\"'.,]+?)[\"']?\s+"
            r"(?:in|and)\s+(?:column\s+)?[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if match:
            return ExtractionInstruction(
                kind="table",
                row_key=match.group(1).strip(),
                column_key=match.group(2).strip(),
            )
        if re.search(r"\btable\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="table")

        match = re.search(
            r"(\w+)(?:st|nd|rd|th)?\s+item\s+(?:in|of)\s+the\s+list",
            text,
            re.IGNORECASE,
        )
        if match:
            return ExtractionInstruction(
                kind="list",
                item_index=_to_ordinal_index(match.group(1)),
            )
        if re.search(r"\blist\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="list")

        match = re.search(
            r"paragraph\s+about\s+[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if match:
            return ExtractionInstruction(kind="paragraph", marker=match.group(1).strip())
        if re.search(r"\bparagraph\b", text, re.IGNORECASE):
            return ExtractionInstruction(kind="paragraph")

        match = re.search(
            r"(href|src|alt|value)\s+of\s+[\"']?([^\"'.,]+)[\"']?",
            text,
            re.IGNORECASE,
        )
        if match:
            return ExtractionInstruction(
                kind="attribute",
                attribute=match.group(1).lower(),
                selector_hint=match.group(2).strip(),
            )

        return ExtractionInstruction(kind="raw")


def parse_task(raw_task: str) -> ParsedTask:
    return TaskDetector().parse(raw_task)
