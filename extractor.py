"""Adaptive extraction engine.

Operates on page content already collected by the browser layer (rendered
HTML + rendered visible text), so it can be exercised deterministically
against local HTML fixtures without a live browser. No website-specific
selectors are used; extraction is driven by generic DOM semantics
(headings, tables, lists, markers) plus a plain-text fallback.

Extraction strategy priority, per element type:
    1. Visible semantic text (rendered_text)
    2. Headings and nearby content (DOM structure)
    3. DOM structure (tables/lists/attributes)
    4. Rendered page text (fallback for word-based ops)
    5. Shadow DOM text, when supplied by the caller (already flattened by
       the browser layer into rendered_text, since BeautifulSoup cannot
       traverse live shadow roots from serialized HTML)
    6. BeautifulSoup/text fallback
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, Tag


@dataclass
class PageContent:
    """Normalized snapshot of a page, produced by the browser layer."""

    html: str
    rendered_text: str = ""
    url: str = ""

    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html, "html.parser")

    def text(self) -> str:
        if self.rendered_text:
            return normalize_whitespace(self.rendered_text)
        return normalize_whitespace(self.soup().get_text(" "))


class ExtractionError(Exception):
    """Raised when the requested information could not be located."""


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace without altering the requested content."""
    return re.sub(r"[ \t ]+", " ", re.sub(r"\r\n|\r", "\n", text)).strip()


def _tokenize(text: str) -> list[str]:
    return text.split()


def first_n_words(text: str, n: int) -> str:
    words = _tokenize(normalize_whitespace(text))
    if not words:
        raise ExtractionError("No text available to extract words from")
    return " ".join(words[: max(n, 0)])


def last_n_words(text: str, n: int) -> str:
    words = _tokenize(normalize_whitespace(text))
    if not words:
        raise ExtractionError("No text available to extract words from")
    return " ".join(words[-max(n, 0):] if n > 0 else [])


def words_between(text: str, start_marker: str, end_marker: str, *, case_sensitive: bool = False) -> str:
    haystack = normalize_whitespace(text)
    flags = 0 if case_sensitive else re.IGNORECASE
    start_match = re.search(re.escape(start_marker), haystack, flags)
    if not start_match:
        raise ExtractionError(f"Start marker not found: {start_marker!r}")
    remainder = haystack[start_match.end():]
    end_match = re.search(re.escape(end_marker), remainder, flags)
    if not end_match:
        raise ExtractionError(f"End marker not found: {end_marker!r}")
    return remainder[: end_match.start()].strip()


def text_before(text: str, marker: str, *, case_sensitive: bool = False) -> str:
    haystack = normalize_whitespace(text)
    flags = 0 if case_sensitive else re.IGNORECASE
    match = re.search(re.escape(marker), haystack, flags)
    if not match:
        raise ExtractionError(f"Marker not found: {marker!r}")
    return haystack[: match.start()].strip()


def text_after(text: str, marker: str, *, case_sensitive: bool = False) -> str:
    haystack = normalize_whitespace(text)
    flags = 0 if case_sensitive else re.IGNORECASE
    match = re.search(re.escape(marker), haystack, flags)
    if not match:
        raise ExtractionError(f"Marker not found: {marker!r}")
    return haystack[match.end():].strip()


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _heading_matches(tag: Tag, needle: str) -> bool:
    return needle.lower() in tag.get_text(" ", strip=True).lower()


def extract_heading_section(soup: BeautifulSoup, heading_text: str) -> str:
    """Return the visible text following a heading, until the next
    sibling heading of equal-or-higher rank."""
    heading = None
    for tag in soup.find_all(_HEADING_TAGS):
        if _heading_matches(tag, heading_text):
            heading = tag
            break
    if heading is None:
        raise ExtractionError(f"Heading not found: {heading_text!r}")

    own_rank = int(heading.name[1])
    parts: list[str] = []
    for sibling in heading.find_next_siblings():
        if sibling.name in _HEADING_TAGS and int(sibling.name[1]) <= own_rank:
            break
        chunk = sibling.get_text(" ", strip=True)
        if chunk:
            parts.append(chunk)
    section_text = " ".join(parts).strip()
    if not section_text:
        raise ExtractionError(f"Heading {heading_text!r} found but has no following content")
    return normalize_whitespace(section_text)


def extract_paragraph(soup: BeautifulSoup, marker: str | None = None, index: int | None = None) -> str:
    paragraphs = [p for p in soup.find_all("p") if p.get_text(strip=True)]
    if not paragraphs:
        raise ExtractionError("No paragraphs found on page")
    if marker:
        for p in paragraphs:
            if marker.lower() in p.get_text(" ", strip=True).lower():
                return normalize_whitespace(p.get_text(" ", strip=True))
        raise ExtractionError(f"No paragraph matching marker: {marker!r}")
    idx = index if index is not None else 0
    try:
        return normalize_whitespace(paragraphs[idx].get_text(" ", strip=True))
    except IndexError as exc:
        raise ExtractionError(f"Paragraph index {idx} out of range (found {len(paragraphs)})") from exc


def extract_list_items(soup: BeautifulSoup, list_marker: str | None = None, item_index: int | None = None) -> Any:
    lists = soup.find_all(["ul", "ol"])
    if not lists:
        raise ExtractionError("No lists found on page")

    target = None
    if list_marker:
        for lst in lists:
            heading = lst.find_previous(_HEADING_TAGS)
            context = (heading.get_text(" ", strip=True) if heading else "") + " " + lst.get_text(" ", strip=True)
            if list_marker.lower() in context.lower():
                target = lst
                break
        if target is None:
            raise ExtractionError(f"No list matching marker: {list_marker!r}")
    else:
        target = lists[0]

    items = [normalize_whitespace(li.get_text(" ", strip=True)) for li in target.find_all("li", recursive=False)]
    items = [i for i in items if i]
    if not items:
        raise ExtractionError("List found but contains no items")
    if item_index is not None:
        try:
            return items[item_index]
        except IndexError as exc:
            raise ExtractionError(f"List item index {item_index} out of range (found {len(items)})") from exc
    return items


def extract_table_cell(
    soup: BeautifulSoup,
    row_key: str | None = None,
    column_key: str | None = None,
    table_marker: str | None = None,
) -> Any:
    tables = soup.find_all("table")
    if not tables:
        raise ExtractionError("No tables found on page")

    target = None
    if table_marker:
        for table in tables:
            heading = table.find_previous(_HEADING_TAGS)
            context = (heading.get_text(" ", strip=True) if heading else "") + " " + table.get_text(" ", strip=True)
            if table_marker.lower() in context.lower():
                target = table
                break
        if target is None:
            raise ExtractionError(f"No table matching marker: {table_marker!r}")
    else:
        target = tables[0]

    rows = target.find_all("tr")
    if not rows:
        raise ExtractionError("Table found but has no rows")

    grid: list[list[str]] = [
        [normalize_whitespace(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
        for row in rows
    ]
    header = grid[0] if grid else []

    if column_key is None and row_key is None:
        return grid

    col_idx = None
    if column_key is not None:
        for i, cell in enumerate(header):
            if column_key.lower() in cell.lower():
                col_idx = i
                break
        if col_idx is None:
            raise ExtractionError(f"Column not found: {column_key!r}")

    if row_key is not None:
        for row in grid[1:]:
            if row and row_key.lower() in row[0].lower():
                if col_idx is not None:
                    if col_idx >= len(row):
                        raise ExtractionError("Column index out of range for matched row")
                    return row[col_idx]
                return row
        raise ExtractionError(f"Row not found: {row_key!r}")

    # Only a column was requested: return all values in that column.
    return [row[col_idx] for row in grid[1:] if col_idx < len(row)]


def extract_attribute(soup: BeautifulSoup, selector_hint: str, attribute: str) -> str:
    """Locate an element via link/image text or id/name hints and return an
    attribute value (e.g. href, src, alt, value)."""
    for tag in soup.find_all(["a", "img", "input", "meta", "time"]):
        label = tag.get_text(" ", strip=True) if tag.name != "img" else tag.get("alt", "")
        haystack = " ".join(filter(None, [label, tag.get("id", ""), tag.get("name", ""), tag.get("title", "")]))
        if selector_hint.lower() in haystack.lower():
            value = tag.get(attribute)
            if value:
                return value
    raise ExtractionError(f"No element matching {selector_hint!r} with attribute {attribute!r}")


@dataclass
class ExtractionInstruction:
    """A normalized description of what to extract, produced by the task
    parser (task_detector.py)."""

    kind: str  # first_n_words | last_n_words | between | before | after |
               # heading | paragraph | list | table | attribute | raw
    n: int | None = None
    start_marker: str | None = None
    end_marker: str | None = None
    marker: str | None = None
    heading: str | None = None
    row_key: str | None = None
    column_key: str | None = None
    table_marker: str | None = None
    list_marker: str | None = None
    item_index: int | None = None
    attribute: str | None = None
    selector_hint: str | None = None


def extract(instruction: ExtractionInstruction, content: PageContent) -> Any:
    """Dispatch to the appropriate extraction strategy for `instruction`."""
    kind = instruction.kind

    if kind == "raw":
        return content.text()

    if kind == "first_n_words":
        return first_n_words(content.text(), instruction.n or 0)

    if kind == "last_n_words":
        return last_n_words(content.text(), instruction.n or 0)

    if kind == "between":
        return words_between(content.text(), instruction.start_marker or "", instruction.end_marker or "")

    if kind == "before":
        return text_before(content.text(), instruction.marker or "")

    if kind == "after":
        return text_after(content.text(), instruction.marker or "")

    soup = content.soup()

    if kind == "heading":
        return extract_heading_section(soup, instruction.heading or "")

    if kind == "paragraph":
        return extract_paragraph(soup, marker=instruction.marker, index=instruction.item_index)

    if kind == "list":
        return extract_list_items(soup, list_marker=instruction.list_marker, item_index=instruction.item_index)

    if kind == "table":
        return extract_table_cell(
            soup,
            row_key=instruction.row_key,
            column_key=instruction.column_key,
            table_marker=instruction.table_marker,
        )

    if kind == "attribute":
        return extract_attribute(soup, instruction.selector_hint or "", instruction.attribute or "href")

    raise ExtractionError(f"Unknown extraction kind: {kind!r}")
