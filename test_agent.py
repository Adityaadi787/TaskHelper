"""Primary test suite for TaskHelper.

Covers imports, configuration, task parsing, every extraction strategy,
answer normalization, Discord command formatting, SQLite memory
operations, browser configuration, error handling/retry logic, simulated
YouTube task parsing, and a real Playwright browser run against
deterministic local HTML fixtures (including JS-rendered and shadow-DOM
content) with proper cleanup.

Run with:  python -m unittest test_agent -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


class TestImports(unittest.TestCase):
    def test_all_modules_import(self):
        import agent  # noqa: F401
        import browser_manager  # noqa: F401
        import config  # noqa: F401
        import discord_bot  # noqa: F401
        import extractor  # noqa: F401
        import main  # noqa: F401
        import memory  # noqa: F401
        import task_detector  # noqa: F401
        import task_engine  # noqa: F401
        import task_executor  # noqa: F401
        import video_tasks  # noqa: F401


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        from config import BrowserConfig, Config

        os.environ.pop("HEADLESS", None)
        cfg = Config()
        self.assertEqual(cfg.log_level, os.getenv("LOG_LEVEL", "INFO").upper())
        self.assertIsInstance(cfg.browser, BrowserConfig)
        self.assertTrue(cfg.browser.headless)
        self.assertEqual(cfg.browser.mode, "local")

    def test_remote_mode_flag(self):
        from config import BrowserConfig

        os.environ["BROWSER_MODE"] = "remote"
        os.environ["BROWSER_WS_ENDPOINT"] = "ws://example.invalid:1234"
        try:
            cfg = BrowserConfig()
            self.assertTrue(cfg.is_remote)
            self.assertEqual(cfg.ws_endpoint, "ws://example.invalid:1234")
        finally:
            os.environ.pop("BROWSER_MODE", None)
            os.environ.pop("BROWSER_WS_ENDPOINT", None)

    def test_bool_and_int_parsing(self):
        from config import _get_bool, _get_int

        os.environ["X_TEST_BOOL"] = "yes"
        os.environ["X_TEST_INT"] = "42"
        try:
            self.assertTrue(_get_bool("X_TEST_BOOL", False))
            self.assertEqual(_get_int("X_TEST_INT", 0), 42)
            self.assertEqual(_get_int("X_TEST_MISSING", 7), 7)
        finally:
            os.environ.pop("X_TEST_BOOL", None)
            os.environ.pop("X_TEST_INT", None)


class TestTaskDetector(unittest.TestCase):
    def setUp(self):
        from task_detector import TaskDetector
        self.detector = TaskDetector()

    def test_basic_search_query(self):
        parsed = self.detector.parse("Search for the tallest mountain in Africa on Wikipedia")
        self.assertEqual(parsed.task_type, "web_search")
        self.assertIn("tallest mountain", parsed.search_query.lower())
        self.assertEqual(parsed.target_site, "wikipedia")

    def test_direct_url_detection(self):
        parsed = self.detector.parse("Open https://example.com/page and read the heading")
        self.assertEqual(parsed.task_type, "direct_url")
        self.assertEqual(parsed.target_url, "https://example.com/page")

    def test_first_n_words_extraction(self):
        parsed = self.detector.parse("Search for cats and give the first 5 words of the article")
        self.assertEqual(parsed.extraction.kind, "first_n_words")
        self.assertEqual(parsed.extraction.n, 5)

    def test_last_n_words_extraction(self):
        parsed = self.detector.parse("Find dogs and give the last three words of the paragraph")
        self.assertEqual(parsed.extraction.kind, "last_n_words")
        self.assertEqual(parsed.extraction.n, 3)

    def test_between_markers_extraction(self):
        parsed = self.detector.parse('Give the text between "START" and "END" on the page')
        self.assertEqual(parsed.extraction.kind, "between")
        self.assertEqual(parsed.extraction.start_marker, "START")
        self.assertEqual(parsed.extraction.end_marker, "END")

    def test_before_after_extraction(self):
        before = self.detector.parse('Give the text before "Founded" on the page')
        self.assertEqual(before.extraction.kind, "before")
        after = self.detector.parse('Give the text after "Founded" on the page')
        self.assertEqual(after.extraction.kind, "after")

    def test_heading_extraction(self):
        parsed = self.detector.parse('Find the text under the heading "History"')
        self.assertEqual(parsed.extraction.kind, "heading")
        self.assertEqual(parsed.extraction.heading, "History")

    def test_table_extraction(self):
        parsed = self.detector.parse("Find the table cell for 1920 in column Population")
        self.assertEqual(parsed.extraction.kind, "table")
        self.assertEqual(parsed.extraction.row_key, "1920")
        self.assertEqual(parsed.extraction.column_key, "Population")

    def test_list_extraction(self):
        parsed = self.detector.parse("Give the second item in the list of notable facts")
        self.assertEqual(parsed.extraction.kind, "list")
        self.assertEqual(parsed.extraction.item_index, 1)

    def test_youtube_task_parsing(self):
        parsed = self.detector.parse(
            'Search youtube for "lofi study music" and find the video titled "Chillhop Radio" '
            'by "ChillhopMusic" and give the view count'
        )
        self.assertEqual(parsed.task_type, "youtube")
        self.assertIsNotNone(parsed.video)
        self.assertEqual(parsed.video.title_hint, "Chillhop Radio")
        self.assertEqual(parsed.video.channel_hint, "ChillhopMusic")
        self.assertEqual(parsed.video.info_request, "view_count")

    def test_youtube_timestamp_and_duration(self):
        parsed = self.detector.parse("On youtube find a video and check what happens at 2:15 for 30 seconds")
        self.assertEqual(parsed.video.timestamp, "2:15")
        self.assertEqual(parsed.video.duration_seconds, 30)

    def test_multi_page_detection(self):
        parsed = self.detector.parse("Search for widgets and check the next page for pricing")
        self.assertTrue(parsed.multi_page)


class TestExtractor(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES_DIR / "sample_page.html").read_text()
        from extractor import PageContent
        self.content = PageContent(html=html, rendered_text="")
        self.soup = self.content.soup()

    def test_first_n_words(self):
        from extractor import first_n_words
        result = first_n_words("The quick brown fox jumps", 3)
        self.assertEqual(result, "The quick brown")

    def test_last_n_words(self):
        from extractor import last_n_words
        result = last_n_words("The quick brown fox jumps", 2)
        self.assertEqual(result, "fox jumps")

    def test_words_between_markers(self):
        from extractor import words_between
        text = self.content.text()
        result = words_between(text, "START-MARKER", "END-MARKER")
        self.assertIn("founded in the year 1850", result)

    def test_text_before_and_after(self):
        from extractor import text_after, text_before
        text = "alpha beta MARKER gamma delta"
        self.assertEqual(text_before(text, "MARKER"), "alpha beta")
        self.assertEqual(text_after(text, "MARKER"), "gamma delta")

    def test_missing_marker_raises(self):
        from extractor import ExtractionError, text_before
        with self.assertRaises(ExtractionError):
            text_before("no marker here", "NOPE")

    def test_heading_section_extraction(self):
        from extractor import extract_heading_section
        result = extract_heading_section(self.soup, "History")
        self.assertIn("founded in the year 1850", result)
        self.assertNotIn("bordered by mountains", result)  # belongs to next section

    def test_paragraph_extraction_by_marker(self):
        from extractor import extract_paragraph
        result = extract_paragraph(self.soup, marker="railway arrived")
        self.assertIn("1902", result)

    def test_list_item_extraction(self):
        from extractor import extract_list_items
        items = extract_list_items(self.soup, list_marker="Notable Facts")
        self.assertEqual(len(items), 3)
        second = extract_list_items(self.soup, list_marker="Notable Facts", item_index=1)
        self.assertIn("bridge spans", second)

    def test_table_cell_extraction(self):
        from extractor import extract_table_cell
        value = extract_table_cell(self.soup, row_key="1920", column_key="Population")
        self.assertEqual(value, "28000")

    def test_table_whole_row(self):
        from extractor import extract_table_cell
        row = extract_table_cell(self.soup, row_key="1950")
        self.assertEqual(row, ["1950", "61000", "Post-war growth"])

    def test_dispatch_extract_first_n_words(self):
        from extractor import ExtractionInstruction, extract
        instr = ExtractionInstruction(kind="first_n_words", n=4)
        result = extract(instr, self.content)
        self.assertEqual(len(result.split()), 4)

    def test_normalize_whitespace(self):
        from extractor import normalize_whitespace
        self.assertEqual(normalize_whitespace("a   b\n\nc \t d"), "a b\n\nc d")

    def test_empty_page_raises(self):
        from extractor import ExtractionError, PageContent, extract_paragraph
        empty = PageContent(html="<html><body></body></html>")
        with self.assertRaises(ExtractionError):
            extract_paragraph(empty.soup())


class TestAgentScoring(unittest.TestCase):
    def test_score_prefers_relevant_and_matching_domain(self):
        from agent import score_candidate

        relevant = score_candidate(
            "Example Mountain - Wikipedia, the free encyclopedia",
            "https://en.wikipedia.org/wiki/Example_Mountain",
            "Example Mountain",
            "wikipedia.org",
        )
        irrelevant = score_candidate(
            "Buy cheap shoes online now",
            "https://unrelated-example.com/ads",
            "Example Mountain",
            "wikipedia.org",
        )
        self.assertGreater(relevant, irrelevant)

    def test_zero_score_without_query_overlap(self):
        from agent import score_candidate
        self.assertEqual(score_candidate("totally unrelated text", "https://x.com", "banana bread recipe", None), 0.0)


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_and_get_task(self):
        from memory import Memory, TaskRecord
        mem = Memory(self.db_path)
        task_id = mem.save_task(TaskRecord(original_task="do X", task_type="web_search"))
        record = mem.get_task(task_id)
        self.assertEqual(record.original_task, "do X")
        self.assertEqual(record.status, "pending")

    def test_update_task(self):
        from memory import Memory, TaskRecord
        mem = Memory(self.db_path)
        task_id = mem.save_task(TaskRecord(original_task="do Y"))
        mem.update_task(task_id, answer="42", status="success", metadata={"k": "v"})
        record = mem.get_task(task_id)
        self.assertEqual(record.answer, "42")
        self.assertEqual(record.status, "success")
        self.assertEqual(record.metadata, {"k": "v"})

    def test_update_unknown_column_raises(self):
        from memory import Memory, TaskRecord
        mem = Memory(self.db_path)
        task_id = mem.save_task(TaskRecord(original_task="do Z"))
        with self.assertRaises(ValueError):
            mem.update_task(task_id, not_a_real_column="x")

    def test_find_similar_only_matches_success(self):
        from memory import Memory, TaskRecord
        mem = Memory(self.db_path)
        mem.save_task(TaskRecord(original_task="a", search_query="python testing", status="failed"))
        mem.save_task(TaskRecord(original_task="b", search_query="python testing", status="success"))
        results = mem.find_similar("python testing")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "success")

    def test_recent_tasks_ordering(self):
        from memory import Memory, TaskRecord
        mem = Memory(self.db_path)
        first = mem.save_task(TaskRecord(original_task="first"))
        second = mem.save_task(TaskRecord(original_task="second"))
        recent = mem.recent_tasks(limit=5)
        self.assertEqual(recent[0].id, second)
        self.assertEqual(recent[1].id, first)

    def test_in_memory_database(self):
        from memory import Memory, TaskRecord
        mem = Memory(":memory:")
        task_id = mem.save_task(TaskRecord(original_task="ephemeral"))
        self.assertIsNotNone(mem.get_task(task_id))


class TestDiscordFormatting(unittest.TestCase):
    def test_escape_quotes_and_backslashes(self):
        from discord_bot import escape_for_add_command
        self.assertEqual(escape_for_add_command('He said "hi"'), 'He said \\"hi\\"')
        self.assertEqual(escape_for_add_command("back\\slash"), "back\\\\slash")

    def test_format_add_command(self):
        from discord_bot import format_add_command
        cmd = format_add_command('What is the "capital"?', "Paris")
        self.assertEqual(cmd, '!add "What is the \\"capital\\"?" "Paris"')

    def test_format_result_message_failure(self):
        from discord_bot import format_result_message
        from task_detector import ParsedTask
        from task_engine import EngineResult
        from task_executor import TaskResult

        parsed = ParsedTask(raw_task="do X")
        result = TaskResult(success=False, error="No results found")
        engine_result = EngineResult(raw_task="do X", parsed=parsed, result=result)
        message = format_result_message(engine_result)
        self.assertIn("Task failed", message)
        self.assertIn("No results found", message)


class TestBrowserManagerConfig(unittest.TestCase):
    def test_local_mode_no_endpoint_required(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig
        manager = BrowserManager(BrowserConfig(mode="local"))
        self.assertFalse(manager.config.is_remote)

    def test_remote_mode_without_endpoint_raises_on_start(self):
        from browser_manager import BrowserLaunchError, BrowserManager
        from config import BrowserConfig

        async def run():
            manager = BrowserManager(BrowserConfig(mode="remote", ws_endpoint=None))
            with self.assertRaises(BrowserLaunchError):
                await manager.start()

        asyncio.run(run())


class TestBrowserManagerRetryLogic(unittest.IsolatedAsyncioTestCase):
    async def test_goto_with_retry_gives_up_after_max_attempts(self):
        from browser_manager import BrowserManager, NavigationError
        from config import BrowserConfig

        manager = BrowserManager(BrowserConfig(max_retries=2))
        fake_page = MagicMock()
        fake_page.goto = AsyncMock(side_effect=RuntimeError("boom"))

        with self.assertRaises(NavigationError):
            await manager.goto_with_retry(fake_page, "https://example.invalid")
        self.assertEqual(fake_page.goto.await_count, 2)

    async def test_goto_with_retry_succeeds_after_transient_failure(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig

        manager = BrowserManager(BrowserConfig(max_retries=3))
        fake_page = MagicMock()
        fake_page.goto = AsyncMock(side_effect=[RuntimeError("boom"), None])

        await manager.goto_with_retry(fake_page, "https://example.invalid")
        self.assertEqual(fake_page.goto.await_count, 2)


class TestPlaywrightRealBrowser(unittest.IsolatedAsyncioTestCase):
    """Launches an actual local Chromium instance via Playwright against a
    deterministic local fixture file. Skipped (with a clear reason, not a
    fake pass) only if no usable Chromium executable can be found."""

    @classmethod
    def setUpClass(cls):
        candidates = [
            os.getenv("PLAYWRIGHT_EXECUTABLE_PATH"),
            "/ms-playwright/chromium-1208/chrome-linux64/chrome",
        ]
        cls.executable_path = next((c for c in candidates if c and Path(c).exists()), None)
        if cls.executable_path is None:
            import shutil
            cls.executable_path = shutil.which("chromium") or shutil.which("google-chrome")
        if cls.executable_path is None:
            raise unittest.SkipTest(
                "No Chromium executable available in this environment; "
                "run `playwright install chromium` or set PLAYWRIGHT_EXECUTABLE_PATH."
            )

    async def test_extracts_from_rendered_local_page(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig
        from extractor import ExtractionInstruction, extract_heading_section, extract

        config = BrowserConfig(headless=True, executable_path=self.executable_path)
        manager = BrowserManager(config)
        await manager.start()
        try:
            page = await manager.new_page()
            fixture_path = (FIXTURES_DIR / "sample_page.html").resolve()
            await manager.goto_with_retry(page, f"file://{fixture_path}")
            content = await manager.extract_page_content(page)

            # JS-rendered content must be present after load.
            self.assertIn("rendered-content-marker", content.rendered_text)

            # rendered_text is the full flattened page text (heading + body),
            # so the first words include the page's leading h1 heading.
            first_words = extract(ExtractionInstruction(kind="first_n_words", n=3), content)
            self.assertEqual(first_words, "Introduction The quick")

            heading_text = extract_heading_section(content.soup(), "Geography")
            self.assertIn("bordered by mountains", heading_text)

            await manager.close_page(page)
        finally:
            await manager.close()

    async def test_shadow_dom_text_is_flattened(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig

        config = BrowserConfig(headless=True, executable_path=self.executable_path)
        manager = BrowserManager(config)
        await manager.start()
        try:
            page = await manager.new_page()
            fixture_path = (FIXTURES_DIR / "shadow_dom.html").resolve()
            await manager.goto_with_retry(page, f"file://{fixture_path}")
            content = await manager.extract_page_content(page)
            self.assertIn("visible-marker-outside", content.rendered_text)
            self.assertIn("shadow-marker-inside", content.rendered_text)
        finally:
            await manager.close()

    async def test_search_results_scoring_against_real_dom(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig
        from agent import BrowserAgent

        config = BrowserConfig(headless=True, executable_path=self.executable_path)
        manager = BrowserManager(config)
        await manager.start()
        try:
            page = await manager.new_page()
            fixture_path = (FIXTURES_DIR / "search_results.html").resolve()
            await manager.goto_with_retry(page, f"file://{fixture_path}")
            agent = BrowserAgent(manager)
            candidates = await agent.collect_candidates(page, "Example Mountain", "wikipedia.org")
            self.assertTrue(candidates)
            self.assertIn("wikipedia.org", candidates[0].url)
        finally:
            await manager.close()

    async def test_browser_cleanup_closes_pages_and_context(self):
        from browser_manager import BrowserManager
        from config import BrowserConfig

        config = BrowserConfig(headless=True, executable_path=self.executable_path)
        manager = BrowserManager(config)
        await manager.start()
        page = await manager.new_page()
        await manager.close()
        self.assertTrue(page.is_closed())


if __name__ == "__main__":
    unittest.main()
