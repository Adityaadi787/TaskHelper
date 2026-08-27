"""Discord interface for TaskHelper.

Kept strictly separate from the browser/extraction engine: this module
only translates Discord messages into TaskEngine.run() calls and formats
the structured result back into chat, plus a safe `!add` command string
for archiving task/answer pairs. No credentials are hard-coded; the bot
token is read exclusively from the DISCORD_TOKEN environment variable via
config.Config.
"""
from __future__ import annotations

import logging

from config import Config
from task_engine import EngineResult, TaskEngine

logger = logging.getLogger("taskhelper.discord")

try:
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:  # pragma: no cover - discord.py is a declared dependency
    DISCORD_AVAILABLE = False


def escape_for_add_command(value: str) -> str:
    """Escape a value so it can be safely embedded inside a double-quoted
    `!add "..." "..."` argument without breaking quoting."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_add_command(description: str, answer: str) -> str:
    """Build the canonical `!add "Task Description" "Extracted Answer"`
    string with both fields safely escaped."""
    return f'!add "{escape_for_add_command(description)}" "{escape_for_add_command(answer)}"'


def format_result_message(engine_result: EngineResult) -> str:
    result = engine_result.result
    if not result.success:
        return f"Task failed: {result.error}"

    answer_text = engine_result.format_answer()
    lines = [f"**Answer:** {answer_text}"]
    if result.url:
        lines.append(f"**Source:** {result.url}")
    lines.append("")
    lines.append(format_add_command(engine_result.raw_task, answer_text))
    return "\n".join(lines)


def create_bot(config: Config | None = None, engine: TaskEngine | None = None) -> "commands.Bot":
    if not DISCORD_AVAILABLE:
        raise RuntimeError("discord.py is not installed. Add it to requirements.txt and pip install.")

    config = config or Config()
    engine = engine or TaskEngine(config)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=config.discord_command_prefix, intents=intents)

    @bot.event
    async def on_ready():  # pragma: no cover - requires live Discord connection
        logger.info("Discord bot connected as %s", bot.user)

    @bot.command(name="task")
    async def task_command(ctx: "commands.Context", *, instruction: str):  # pragma: no cover
        await ctx.send(f"Working on: {instruction}")
        try:
            result = await engine.run(instruction)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the bot
            logger.exception("Unhandled error running task from Discord")
            await ctx.send(f"Internal error while running task: {exc}")
            return
        await ctx.send(format_result_message(result))

    @bot.command(name="add")
    async def add_command(ctx: "commands.Context", description: str, answer: str):  # pragma: no cover
        await ctx.send(f"Recorded: {description!r} -> {answer!r}")

    return bot


def run_bot() -> None:
    """Blocking entry point for running the Discord bot standalone."""
    config = Config()
    if not config.discord_token:
        raise RuntimeError(
            "DISCORD_TOKEN is not set. Add it to your environment or .env file "
            "before starting the Discord bot."
        )
    bot = create_bot(config)
    bot.run(config.discord_token)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_bot()
