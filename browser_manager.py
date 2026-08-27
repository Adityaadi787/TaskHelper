"""Async Playwright browser lifecycle management.

Supports two modes, selected purely via environment configuration
(config.BrowserConfig):

    local  - launches a local Chromium instance (must be installed ahead of
             time via `playwright install chromium`; this module never
             downloads a browser at runtime).
    remote - connects to an existing Playwright-compatible browser server
             over a WebSocket endpoint (BROWSER_WS_ENDPOINT), useful for
             low-resource hosts that cannot run a browser locally.

Provides bounded retries for navigation, popup/new-tab tracking, and
guarantees page/context/browser cleanup even on failure.
"""
from __future__ import annotations

import asyncio
import logging

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from config import BrowserConfig
from extractor import PageContent

logger = logging.getLogger("taskhelper.browser")


class BrowserLaunchError(RuntimeError):
    """Raised when the browser cannot be launched or connected to."""


class NavigationError(RuntimeError):
    """Raised when a page fails to navigate after all retries."""


class BrowserManager:
    """Owns a single Playwright browser + context for the process lifetime.

    Usage:
        async with BrowserManager(config) as manager:
            page = await manager.new_page()
            ...
    """

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
        if self._browser is not None:
            return
        try:
            self._playwright = await async_playwright().start()
        except Exception as exc:  # pragma: no cover - environment dependent
            raise BrowserLaunchError(f"Failed to start Playwright driver: {exc}") from exc

        try:
            if self.config.is_remote:
                if not self.config.ws_endpoint:
                    raise BrowserLaunchError(
                        "BROWSER_MODE=remote requires BROWSER_WS_ENDPOINT to be set"
                    )
                logger.info("Connecting to remote browser at %s", self.config.ws_endpoint)
                self._browser = await self._playwright.chromium.connect(self.config.ws_endpoint)
            else:
                launch_kwargs: dict = {"headless": self.config.headless}
                if self.config.executable_path:
                    launch_kwargs["executable_path"] = self.config.executable_path
                logger.info("Launching local Chromium (headless=%s)", self.config.headless)
                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:
            await self._safe_stop_playwright()
            hint = (
                "Local Chromium executable not found. Run `playwright install chromium` "
                "as a deployment/setup step before starting the app."
                if not self.config.is_remote
                else "Could not reach the remote browser endpoint. Verify BROWSER_WS_ENDPOINT."
            )
            raise BrowserLaunchError(f"{exc}. {hint}") from exc

        try:
            self._context = await self._browser.new_context(
                viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
                user_agent=self.config.user_agent,
            )
            self._context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
            self._context.set_default_timeout(self.config.action_timeout_ms)
        except Exception as exc:
            await self.close()
            raise BrowserLaunchError(f"Failed to create browser context: {exc}") from exc

    async def _safe_stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("Error while stopping playwright driver", exc_info=True)
            self._playwright = None

    async def new_page(self) -> Page:
        if self._context is None:
            raise BrowserLaunchError("BrowserManager not started; call start() first")
        page = await self._context.new_page()
        self._pages.append(page)

        # Track popups/new tabs so callers can discover them without
        # website-specific handling.
        page.on("popup", lambda popup: self._pages.append(popup))
        return page

    async def goto_with_retry(self, page: Page, url: str, *, wait_until: str = "load") -> None:
        """Navigate with bounded retries and navigation-failure recovery."""
        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                await page.goto(url, wait_until=wait_until, timeout=self.config.navigation_timeout_ms)
                return
            except PlaywrightTimeoutError as exc:
                last_exc = exc
                logger.warning("Navigation to %s timed out (attempt %d/%d)", url, attempt, self.config.max_retries)
            except Exception as exc:
                last_exc = exc
                logger.warning("Navigation to %s failed (attempt %d/%d): %s", url, attempt, self.config.max_retries, exc)
            if attempt < self.config.max_retries:
                await asyncio.sleep(min(2 ** attempt, 8))
        raise NavigationError(f"Failed to navigate to {url} after {self.config.max_retries} attempts: {last_exc}")

    async def extract_page_content(self, page: Page) -> PageContent:
        """Snapshot the current page as HTML + flattened visible text.

        The visible-text flattening walks light DOM and open shadow roots
        via page.evaluate so that shadow-DOM content (inaccessible to a
        BeautifulSoup pass over page.content()) is still available to the
        extractor as text.
        """
        html = await page.content()
        rendered_text = await page.evaluate(_FLATTEN_TEXT_JS)
        return PageContent(html=html, rendered_text=rendered_text, url=page.url)

    async def close_page(self, page: Page) -> None:
        try:
            if page in self._pages:
                self._pages.remove(page)
            if not page.is_closed():
                await page.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.debug("Error closing page", exc_info=True)

    async def close(self) -> None:
        for page in list(self._pages):
            await self.close_page(page)
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # pragma: no cover
                logger.debug("Error closing context", exc_info=True)
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # pragma: no cover
                logger.debug("Error closing browser", exc_info=True)
            self._browser = None
        await self._safe_stop_playwright()


_FLATTEN_TEXT_JS = """
() => {
    function collect(root, parts) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const value = node.nodeValue.trim();
            if (value) parts.push(value);
        }
        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (const el of all) {
            if (el.shadowRoot) collect(el.shadowRoot, parts);
        }
    }
    const parts = [];
    collect(document.body || document.documentElement, parts);
    return parts.join(' ');
}
"""
