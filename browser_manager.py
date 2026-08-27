"""Reusable async Playwright lifecycle and navigation management."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

from config import BrowserConfig
from extractor import PageContent

logger = logging.getLogger("taskhelper.browser")


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    return parsed._replace(query="[redacted-query]").geturl()


class BrowserLaunchError(RuntimeError):
    pass


class NavigationError(RuntimeError):
    pass


class BrowserManager:
    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: list[Page] = []

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        if self._browser is not None and self._context is not None:
            return
        try:
            self._playwright = await async_playwright().start()
            if self.config.is_remote:
                self._browser = await self._playwright.chromium.connect(self.config.ws_endpoint)
            else:
                kwargs = {"headless": self.config.headless}
                executable = self.config.executable_path or shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chromium-browser")
                if executable:
                    kwargs["executable_path"] = executable
                self._browser = await self._playwright.chromium.launch(**kwargs)
            self._context = await self._browser.new_context(
                viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                user_agent=self.config.user_agent,
            )
            self._context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
            self._context.set_default_timeout(self.config.action_timeout_ms)
        except Exception as exc:
            await self.close()
            hint = (
                "Install Chromium during setup with `playwright install chromium`."
                if not self.config.is_remote else
                "Verify BROWSER_WS_ENDPOINT and the remote Playwright server."
            )
            raise BrowserLaunchError(f"Browser startup failed: {exc}. {hint}") from exc

    async def new_page(self) -> Page:
        if self._context is None:
            raise BrowserLaunchError("BrowserManager is not started")
        page = await self._context.new_page()
        self._pages.append(page)
        page.on("popup", lambda popup: self._pages.append(popup))
        return page

    async def _goto_file(self, page: Page, url: str) -> None:
        parsed = urlparse(url)
        path = Path(parsed.path)
        if not path.exists() or not path.is_file():
            raise NavigationError(f"Local file does not exist: {path}")
        html = path.read_text(encoding="utf-8")
        # Some managed Chromium environments block file:// navigation. Loading
        # the exact fixture through set_content still executes its JavaScript.
        await page.set_content(html, wait_until="load", timeout=self.config.navigation_timeout_ms)

    async def goto_with_retry(self, page: Page, url: str, *, wait_until: str = "load") -> None:
        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                if urlparse(url).scheme == "file":
                    await self._goto_file(page, url)
                else:
                    await page.goto(url, wait_until=wait_until, timeout=self.config.navigation_timeout_ms)
                await self._delay()
                return
            except (PlaywrightTimeoutError, Exception) as exc:
                last_exc = exc
                logger.warning("Navigation failed (%d/%d) for %s: %s", attempt, self.config.max_retries, _safe_url(url), exc)
                if attempt < self.config.max_retries:
                    await asyncio.sleep(min(2 ** attempt, 8))
        raise NavigationError(f"Failed to navigate to {_safe_url(url)} after {self.config.max_retries} attempts: {last_exc}") from last_exc

    async def _delay(self) -> None:
        if self.config.interaction_delay_ms:
            await asyncio.sleep(self.config.interaction_delay_ms / 1000)

    async def extract_page_content(self, page: Page) -> PageContent:
        """Snapshot the main document plus accessible iframe content.

        Cross-origin or otherwise inaccessible frames are skipped because
        browser security must not be bypassed.
        """
        html = await page.content()
        rendered_parts = [await page.evaluate(_FLATTEN_TEXT_JS)]
        frame_html: list[str] = []
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                frame_html.append(await frame.content())
                frame_text = await frame.evaluate(_FLATTEN_TEXT_JS)
                if frame_text:
                    rendered_parts.append(frame_text)
            except Exception:
                logger.debug("Accessible iframe snapshot unavailable; skipping frame", exc_info=True)
        if frame_html:
            html += "\n<!-- TaskHelper accessible iframe snapshots -->\n" + "\n".join(frame_html)
        rendered_text = " ".join(part for part in rendered_parts if part)
        return PageContent(html=html, rendered_text=rendered_text, url=page.url)

    async def close_page(self, page: Page) -> None:
        try:
            if page in self._pages:
                self._pages.remove(page)
            if not page.is_closed():
                await page.close()
        except Exception:
            logger.debug("Page cleanup failed", exc_info=True)

    async def close(self) -> None:
        for page in list(self._pages):
            await self.close_page(page)
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.debug("Context cleanup failed", exc_info=True)
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Browser cleanup failed", exc_info=True)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Playwright cleanup failed", exc_info=True)
            self._playwright = None


_FLATTEN_TEXT_JS = r"""
() => {
  const parts = [];
  const seen = new Set();
  function collect(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const value = node.nodeValue.trim();
      if (value) parts.push(value);
    }
    const elements = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of elements) {
      if (el.shadowRoot && !seen.has(el.shadowRoot)) { seen.add(el.shadowRoot); collect(el.shadowRoot); }
    }
  }
  collect(document.body || document.documentElement);
  return parts.join(' ');
}
"""
