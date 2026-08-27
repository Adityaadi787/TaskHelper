"""Centralized, environment-variable-based configuration for TaskHelper.

No secrets are hard-coded here. Everything is read from the environment
(optionally loaded from a local .env file via python-dotenv) with sane,
resource-conscious defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class BrowserConfig:
    """Configuration for the Playwright browser lifecycle."""

    mode: str = field(default_factory=lambda: os.getenv("BROWSER_MODE", "local").lower())
    ws_endpoint: str | None = field(default_factory=lambda: os.getenv("BROWSER_WS_ENDPOINT") or None)
    executable_path: str | None = field(
        default_factory=lambda: os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or None
    )
    headless: bool = field(default_factory=lambda: _get_bool("HEADLESS", True))
    viewport_width: int = field(default_factory=lambda: _get_int("VIEWPORT_WIDTH", 1366))
    viewport_height: int = field(default_factory=lambda: _get_int("VIEWPORT_HEIGHT", 768))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TaskHelper/1.0",
        )
    )
    navigation_timeout_ms: int = field(default_factory=lambda: _get_int("NAVIGATION_TIMEOUT_MS", 20000))
    action_timeout_ms: int = field(default_factory=lambda: _get_int("ACTION_TIMEOUT_MS", 10000))
    max_retries: int = field(default_factory=lambda: _get_int("MAX_RETRIES", 3))

    @property
    def is_remote(self) -> bool:
        return self.mode == "remote"


@dataclass(frozen=True)
class Config:
    discord_token: str | None = field(default_factory=lambda: os.getenv("DISCORD_TOKEN") or None)
    discord_command_prefix: str = field(default_factory=lambda: os.getenv("DISCORD_COMMAND_PREFIX", "!"))
    target_url: str | None = field(default_factory=lambda: os.getenv("TARGET_URL") or None)
    default_search_url: str = field(
        default_factory=lambda: os.getenv("DEFAULT_SEARCH_URL", "https://search.yahoo.com/search?p={query}")
    )
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/taskhelper.db"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    browser: BrowserConfig = field(default_factory=BrowserConfig)

    def ensure_database_dir(self) -> None:
        db_dir = Path(self.database_path).expanduser().resolve().parent
        db_dir.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
