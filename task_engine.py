"""Top-level orchestration: natural language in, structured/validated
result out. Wires together task_detector -> task_executor -> memory, and
is runnable independently of any Discord bot (see main.py)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from browser_manager import BrowserManager
from config import Config
from memory import Memory, TaskRecord
from task_detector import TaskDetector, ParsedTask
from task_executor import TaskExecutor, TaskResult

logger = logging.getLogger("taskhelper.engine")


@dataclass
class EngineResult:
    raw_task: str
    parsed: ParsedTask
    result: TaskResult
    task_id: int | None = None

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def answer(self) -> Any:
        return self.result.answer

    def format_answer(self) -> str:
        answer = self.result.answer
        if isinstance(answer, list):
            return "\n".join(f"- {item}" for item in answer)
        if isinstance(answer, dict):
            return "\n".join(f"{k}: {v}" for k, v in answer.items())
        return str(answer) if answer is not None else ""


class TaskEngine:
    """High-level entry point used by both the Discord bot and CLI/tests."""

    def __init__(self, config: Config | None = None, memory: Memory | None = None):
        self.config = config or Config()
        self.detector = TaskDetector()
        self.memory = memory or Memory(self.config.database_path)

    def close(self) -> None:
        """Close the engine-owned SQLite connection."""
        self.memory.close()

    async def run(self, raw_task: str) -> EngineResult:
        parsed = self.detector.parse(raw_task)
        record = TaskRecord(
            original_task=raw_task,
            task_type=parsed.task_type,
            search_query=parsed.search_query,
            points=self._extract_points(raw_task),
            metadata={"section_hint": parsed.section_hint} if parsed.section_hint else {},
            status="pending",
        )
        task_id = self.memory.save_task(record)

        try:
            async with BrowserManager(self.config.browser) as manager:
                executor = TaskExecutor(manager, self.config)
                result = await executor.execute(parsed)
        except Exception as exc:  # browser/database-independent top-level safety boundary
            logger.exception("Task engine failed before task execution completed")
            result = TaskResult(success=False, error=f"Task engine error: {exc}")

        self.memory.update_task(
            task_id,
            answer=self._serialize_answer(result.answer),
            url=result.url,
            location=result.location or parsed.section_hint,
            rule=parsed.extraction.kind,
            points=record.points,
            metadata={**record.metadata, **result.metadata},
            status="success" if result.success else "failed",
            error=result.error,
        )

        return EngineResult(raw_task=raw_task, parsed=parsed, result=result, task_id=task_id)

    @staticmethod
    def _extract_points(raw_task: str) -> float | None:
        match = re.search(
            r"\b(?:(?:points?|pts?)\s*[:=]?\s*(\d+(?:\.\d+)?)|"
            r"(\d+(?:\.\d+)?)\s*(?:points?|pts?))\b",
            raw_task,
            re.IGNORECASE,
        )
        return float(match.group(1) or match.group(2)) if match else None

    @staticmethod
    def _serialize_answer(answer: Any) -> str | None:
        if answer is None:
            return None
        if isinstance(answer, (list, dict)):
            import json
            return json.dumps(answer)
        return str(answer)
