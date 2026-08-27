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
from task_detector import ParsedTask, VideoHints
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
        if task.multi_page:
            content = await self._follow_pagination(page, content, task)
        return self._extract_and_validate(content, task, url=task.target_url)

    async def _execute_web_search(self, page, task: ParsedTask) -> TaskResult:
        if not task.search_query:
            raise TaskExecutionError("Could not determine a search query from the task text.")

        await self.agent.perform_search(page, task.search_query, self.config.default_search_url)
        candidates = await self.agent.collect_candidates(page, task.search_query, task.target_site, self.config.max_search_results)
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
        """Follow up to Config.max_pages generic pagination links.

        The current page is always included. Links are selected using rel=next
        first, then conservative visible-text/aria-label hints, while avoiding
        duplicate URLs and obvious non-page links.
        """
        pages = [content]
        visited = {content.url or page.url}
        for _ in range(max(0, self.config.max_pages - 1)):
            try:
                next_link = await page.eval_on_selector(
                    "a[href]",
                    """els => {
                        const links = Array.from(document.querySelectorAll('a[href]'));
                        const rel = links.find(a => /\bnext\b/i.test(a.rel || ''));
                        if (rel) return rel.href;
                        const score = a => {
                          const t = `${a.innerText || ''} ${a.getAttribute('aria-label') || ''}`.trim();
                          if (/^next$/i.test(t)) return 5;
                          if (/next page|older|more results/i.test(t)) return 4;
                          return 0;
                        };
                        return links.map(a => ({a, s: score(a)})).sort((x,y)=>y.s-x.s)[0]?.s ? links.map(a=>({a,s:score(a)})).sort((x,y)=>y.s-x.s)[0].a.href : null;
                    }""",
                )
            except Exception:
                break
            if not next_link or next_link in visited:
                break
            visited.add(next_link)
            try:
                await self.manager.goto_with_retry(page, next_link)
                pages.append(await self.agent.get_content(page))
            except NavigationError:
                logger.warning("Pagination navigation failed; keeping collected pages")
                break
        if len(pages) == 1:
            return content
        from bs4 import BeautifulSoup
        root_soup = BeautifulSoup("<html><body><main id='taskhelper-pages'></main></body></html>", "html.parser")
        main = root_soup.select_one("main")
        texts=[]
        for item in pages:
            soup=BeautifulSoup(item.html, "html.parser")
            body=soup.body or soup
            for child in list(body.children):
                if getattr(child, "name", None): main.append(child)
            texts.append(item.rendered_text)
        return PageContent(html=str(root_soup), rendered_text=" ".join(texts), url=pages[-1].url)

    async def _execute_youtube(self, page, task: ParsedTask) -> TaskResult:
        if not task.search_query and not (task.video and task.video.title_hint):
            raise TaskExecutionError("Could not determine what to search for on YouTube.")
        handler = VideoTaskHandler(self.manager, self.agent)
        hints = task.video or VideoHints()
        query = task.search_query or hints.title_hint
        if not query:
            raise TaskExecutionError("Could not determine what to search for on YouTube.")
        video = await handler.search_and_identify(page, query, hints)
        content = await self.agent.get_content(page)

        if hints.info_request or hints.timestamp:
            info = await handler.extract_requested_info(content, hints, page)
            return TaskResult(
                success=True,
                answer=info,
                url=video.url,
                location=hints.info_request or "timestamp",
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
