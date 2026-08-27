"""Centralized, validated environment configuration for TaskHelper."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class BrowserConfig:
    mode: str = field(default_factory=lambda: os.getenv("BROWSER_MODE", "local").strip().lower())
    ws_endpoint: str | None = field(default_factory=lambda: os.getenv("BROWSER_WS_ENDPOINT") or None)
    executable_path: str | None = field(default_factory=lambda: os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or None)
    headless: bool = field(default_factory=lambda: _get_bool("HEADLESS", True))
    viewport_width: int = field(default_factory=lambda: _get_int("VIEWPORT_WIDTH", 1366))
    viewport_height: int = field(default_factory=lambda: _get_int("VIEWPORT_HEIGHT", 768))
    user_agent: str = field(default_factory=lambda: os.getenv("USER_AGENT") or "Mozilla/5.0 TaskHelper/1.0")
    navigation_timeout_ms: int = field(default_factory=lambda: _get_int("NAVIGATION_TIMEOUT_MS", 20000))
    action_timeout_ms: int = field(default_factory=lambda: _get_int("ACTION_TIMEOUT_MS", 10000))
    max_retries: int = field(default_factory=lambda: _get_int("MAX_RETRIES", 3))
    interaction_delay_ms: int = field(default_factory=lambda: _get_int("INTERACTION_DELAY_MS", 0))

    def __post_init__(self) -> None:
        if self.mode not in {"local", "remote"}:
            raise ValueError("BROWSER_MODE must be 'local' or 'remote'")
        if self.viewport_width < 320 or self.viewport_height < 240:
            raise ValueError("Viewport must be at least 320x240")
        if self.navigation_timeout_ms <= 0 or self.action_timeout_ms <= 0:
            raise ValueError("Browser timeouts must be positive")
        if not 1 <= self.max_retries <= 5:
            raise ValueError("MAX_RETRIES must be between 1 and 5")
        if self.interaction_delay_ms < 0 or self.interaction_delay_ms > 60000:
            raise ValueError("INTERACTION_DELAY_MS must be between 0 and 60000")

    @property
    def is_remote(self) -> bool:
        return self.mode == "remote"


@dataclass(frozen=True)
class Config:
    discord_token: str | None = field(default_factory=lambda: os.getenv("DISCORD_TOKEN") or None)
    discord_command_prefix: str = field(default_factory=lambda: os.getenv("DISCORD_COMMAND_PREFIX", "!"))
    target_url: str | None = field(default_factory=lambda: os.getenv("TARGET_URL") or None)
    default_search_url: str = field(default_factory=lambda: os.getenv("DEFAULT_SEARCH_URL", "https://duckduckgo.com/html/?q={query}"))
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/taskhelper.db"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    max_search_results: int = field(default_factory=lambda: _get_int("MAX_SEARCH_RESULTS", 10))
    max_pages: int = field(default_factory=lambda: _get_int("MAX_PAGES", 5))
    memory_max_age_hours: float = field(default_factory=lambda: _get_float("MEMORY_MAX_AGE_HOURS", 24.0))
    browser: BrowserConfig = field(default_factory=BrowserConfig)

    def __post_init__(self) -> None:
        if "{query}" not in self.default_search_url:
            raise ValueError("DEFAULT_SEARCH_URL must contain a {query} placeholder")
        if not 1 <= self.max_search_results <= 50:
            raise ValueError("MAX_SEARCH_RESULTS must be between 1 and 50")
        if not 1 <= self.max_pages <= 20:
            raise ValueError("MAX_PAGES must be between 1 and 20")
        if self.memory_max_age_hours <= 0:
            raise ValueError("MEMORY_MAX_AGE_HOURS must be positive")

    def ensure_database_dir(self) -> None:
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
