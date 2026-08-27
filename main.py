"""TaskHelper entry point.

Usage:
    python main.py "search for X and give the first 10 words"   # run one task via CLI
    python main.py --discord                                     # start the Discord bot
    python main.py --recent                                      # show recent memory entries
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from config import Config
from memory import Memory
from task_engine import TaskEngine


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run_cli_task(task_text: str) -> int:
    config = Config()
    config.ensure_database_dir()
    engine = TaskEngine(config)
    result = await engine.run(task_text)

    if result.success:
        print(f"Answer: {result.result.answer}")
        if result.result.url:
            print(f"Source: {result.result.url}")
        return 0
    print(f"Task failed: {result.result.error}", file=sys.stderr)
    return 1


def _show_recent(limit: int = 10) -> int:
    config = Config()
    memory = Memory(config.database_path)
    for record in memory.recent_tasks(limit=limit):
        print(f"[{record.id}] ({record.status}) {record.original_task!r} -> {record.answer!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TaskHelper: browser automation + extraction agent")
    parser.add_argument("task", nargs="?", help="Natural-language task to run")
    parser.add_argument("--discord", action="store_true", help="Start the Discord bot instead")
    parser.add_argument("--recent", action="store_true", help="Show recent tasks from memory")
    args = parser.parse_args(argv)

    config = Config()
    _setup_logging(config.log_level)

    if args.discord:
        from discord_bot import run_bot
        run_bot()
        return 0

    if args.recent:
        return _show_recent()

    if not args.task:
        parser.print_help()
        return 1

    return asyncio.run(_run_cli_task(args.task))


if __name__ == "__main__":
    raise SystemExit(main())
