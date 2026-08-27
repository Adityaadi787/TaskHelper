"""SQLite-backed persistent memory for TaskHelper.

Stores completed/attempted tasks: the original instruction, parsed task
type, search query, extracted answer, source URL, the section/location the
answer was found in, arbitrary metadata (JSON), a timestamp, and a
success/failure status with optional error information.

The database path is configurable via Config.database_path. Nothing here
trusts stale data blindly: callers should treat rows as historical record,
not as ground truth to skip re-verification.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("taskhelper.memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_task TEXT NOT NULL,
    task_type TEXT,
    search_query TEXT,
    answer TEXT,
    url TEXT,
    location TEXT,
    metadata TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_task_type ON tasks(task_type);
"""


@dataclass
class TaskRecord:
    original_task: str
    task_type: str | None = None
    search_query: str | None = None
    answer: str | None = None
    url: str | None = None
    location: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: str | None = None
    id: int | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TaskRecord":
        return cls(
            id=row["id"],
            original_task=row["original_task"],
            task_type=row["task_type"],
            search_query=row["search_query"],
            answer=row["answer"],
            url=row["url"],
            location=row["location"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            status=row["status"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class Memory:
    """Thin, dependency-free wrapper around a SQLite tasks table."""

    def __init__(self, database_path: str = "data/taskhelper.db"):
        self.database_path = database_path
        self._is_memory = database_path == ":memory:"
        self._persistent_conn: sqlite3.Connection | None = None
        if not self._is_memory:
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        else:
            # sqlite3.connect(":memory:") creates a brand new, isolated
            # database on every call, so a single connection must be kept
            # alive for the lifetime of this Memory instance.
            self._persistent_conn = sqlite3.connect(":memory:", timeout=10, check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            conn = self._persistent_conn
            try:
                yield conn
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            return

        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(SCHEMA)
        except sqlite3.Error as exc:
            logger.error("Failed to initialize database schema at %s: %s", self.database_path, exc)
            raise

    def save_task(self, record: TaskRecord) -> int:
        """Insert a new task record and return its row id."""
        now = time.time()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO tasks (
                        original_task, task_type, search_query, answer, url,
                        location, metadata, status, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.original_task,
                        record.task_type,
                        record.search_query,
                        record.answer,
                        record.url,
                        record.location,
                        json.dumps(record.metadata or {}),
                        record.status,
                        record.error,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)
        except sqlite3.Error as exc:
            logger.error("Failed to save task record: %s", exc)
            raise

    def update_task(self, task_id: int, **fields: Any) -> None:
        """Update arbitrary columns on an existing task row."""
        if not fields:
            return
        allowed = {
            "task_type", "search_query", "answer", "url", "location",
            "metadata", "status", "error",
        }
        cols = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Cannot update unknown column: {key}")
            if key == "metadata":
                value = json.dumps(value or {})
            cols.append(f"{key} = ?")
            values.append(value)
        cols.append("updated_at = ?")
        values.append(time.time())
        values.append(task_id)
        try:
            with self._connect() as conn:
                conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE id = ?", values)
        except sqlite3.Error as exc:
            logger.error("Failed to update task %s: %s", task_id, exc)
            raise

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return TaskRecord.from_row(row) if row else None

    def find_similar(self, search_query: str | None, task_type: str | None = None, limit: int = 5) -> list[TaskRecord]:
        """Look up prior tasks with a similar query, most recent first.

        Callers should verify freshness before trusting these results for
        anything time-sensitive.
        """
        if not search_query:
            return []
        sql = "SELECT * FROM tasks WHERE search_query LIKE ? AND status = 'success'"
        params: list[Any] = [f"%{search_query}%"]
        if task_type:
            sql += " AND task_type = ?"
            params.append(task_type)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [TaskRecord.from_row(r) for r in rows]

    def recent_tasks(self, limit: int = 20) -> list[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [TaskRecord.from_row(r) for r in rows]

    def delete_task(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
