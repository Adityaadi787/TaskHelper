"""Executes a single ParsedTask end-to-end against a live browser:
search/navigate, pick the relevant candidate, extract, and validate.

Kept separate from Discord and from task_engine's memory/reporting duties
so it can be driven directly in tests or a CLI without any bot framework.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent import BrowserAgent
from browser_manager import BrowserManager, NavigationError
from config import Config
from extractor import ExtractionError, PageContent, extract
from task_detector import ParsedTask
from video_tasks import VideoTaskError, VideoTaskHandler

logger = logging.getLogger("taskhelper.task_executor")


class TaskExecutionError(Exception):
    """Raised with the real, actionable reason a task could not complete."""


@dataclass
class TaskResult:
    success: bool
    answer: Any = None
    url: str | None = None
    location: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskExecutor:
    """Drives BrowserAgent/VideoTaskHandler for one ParsedTask."""

    def __init__(self, manager: BrowserManager, config: Config | None = None):
        self.manager = manager
        self.config = config or Config()
        self.agent = BrowserAgent(manager)

    async def execute(self, task: ParsedTask) -> TaskResult:
        page = await self.manager.new_page()
        try:
            if task.task_type == "youtube":
                return await self._execute_youtube(page, task)
            if task.task_type == "direct_url":
                return await self._execute_direct_url(page, task)
            return await self._execute_web_search(page, task)
        except (NavigationError, ExtractionError, VideoTaskError, TaskExecutionError) as exc:
            logger.warning("Task failed: %s", exc)
            return TaskResult(success=False, error=str(exc), url=page.url if not page.is_closed() else None)
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors, don't hide them
            logger.exception("Unexpected error executing task")
            return TaskResult(success=False, error=f"Unexpected error: {exc}")
        finally:
            await self.manager.close_page(page)

    async def _execute_direct_url(self, page, task: ParsedTask) -> TaskResult:
        await self.manager.goto_with_retry(page, task.target_url)
        content = await self.agent.get_content(page)
        return self._extract_and_validate(content, task)

    async def _execute_web_search(self, page, task: ParsedTask) -> TaskResult:
        if not task.search_query:
            raise TaskExecutionError("Could not determine a search query from the task text.")

        await self.agent.perform_search(page, task.search_query, self.config.default_search_url)
        candidates = await self.agent.collect_candidates(page, task.search_query, task.target_site)
        if not candidates:
            raise TaskExecutionError(
                f"No relevant search results found for query {task.search_query!r}."
            )
        await self.agent.open_candidate(page, candidates[0])
        content = await self.agent.get_content(page)

        if task.multi_page:
            content = await self._follow_pagination(page, content, task)

        return self._extract_and_validate(content, task, url=page.url, chosen_score=candidates[0].score)

    async def _follow_pagination(self, page, content: PageContent, task: ParsedTask) -> PageContent:
        """Best-effort: try to find a 'next page' link generically and
        merge its text in, for tasks that explicitly need multi-page
        extraction. Never assumes a specific site's pagination markup."""
        try:
            next_link = await page.eval_on_selector_all(
                "a[href]",
                "els => { const m = els.find(e => /next|more/i.test(e.innerText)); return m ? m.href : null; }",
            )
        except Exception:
            return content
        if not next_link:
            return content
        try:
            await self.manager.goto_with_retry(page, next_link)
            next_content = await self.agent.get_content(page)
            merged = PageContent(
                html=content.html + next_content.html,
                rendered_text=(content.rendered_text + " " + next_content.rendered_text).strip(),
                url=next_content.url,
            )
            return merged
        except NavigationError:
            return content

    async def _execute_youtube(self, page, task: ParsedTask) -> TaskResult:
        if not task.search_query and not (task.video and task.video.title_hint):
            raise TaskExecutionError("Could not determine what to search for on YouTube.")
        handler = VideoTaskHandler(self.manager, self.agent)
        query = task.search_query or task.video.title_hint
        video = await handler.search_and_identify(page, query, task.video)
        content = await self.agent.get_content(page)

        if task.video and (task.video.info_request or task.video.timestamp):
            info = handler.extract_requested_info(content, task.video)
            return TaskResult(
                success=True,
                answer=info,
                url=video.url,
                location=task.video.info_request or "timestamp",
                metadata={"title": video.title, "channel": video.channel},
            )

        return self._extract_and_validate(content, task, url=video.url, extra_meta={"title": video.title, "channel": video.channel})

    def _extract_and_validate(
        self,
        content: PageContent,
        task: ParsedTask,
        url: str | None = None,
        chosen_score: float | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> TaskResult:
        answer = extract(task.extraction, content)
        if answer is None or (isinstance(answer, str) and not answer.strip()) or (isinstance(answer, list) and not answer):
            raise TaskExecutionError("Extraction produced an empty result; the requested content was not found.")

        metadata: dict[str, Any] = dict(extra_meta or {})
        if chosen_score is not None:
            metadata["candidate_relevance_score"] = chosen_score

        return TaskResult(
            success=True,
            answer=answer,
            url=url or content.url,
            location=task.extraction.kind,
            metadata=metadata,
        )
