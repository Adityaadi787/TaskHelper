"""Persistent SQLite memory with explicit freshness semantics."""
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
 id INTEGER PRIMARY KEY AUTOINCREMENT, original_task TEXT NOT NULL, task_type TEXT,
 search_query TEXT, answer TEXT, location TEXT, rule TEXT, url TEXT, points REAL,
 metadata TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_query ON tasks(search_query);
"""

@dataclass
class TaskRecord:
    original_task: str
    task_type: str | None = None
    search_query: str | None = None
    answer: str | None = None
    location: str | None = None
    rule: str | None = None
    url: str | None = None
    points: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: str | None = None
    id: int | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TaskRecord":
        return cls(
            id=row["id"], original_task=row["original_task"], task_type=row["task_type"],
            search_query=row["search_query"], answer=row["answer"], location=row["location"],
            rule=row["rule"], url=row["url"], points=row["points"],
            metadata=json.loads(row["metadata"] or "{}"), status=row["status"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

class Memory:
    def __init__(self, database_path: str = "data/taskhelper.db"):
        self.database_path = database_path
        self._is_memory = database_path == ":memory:"
        self._persistent_conn = None
        if self._is_memory:
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        else:
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            conn = self._persistent_conn
            try:
                yield conn
                conn.commit()
            except sqlite3.Error:
                conn.rollback(); raise
            return
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn; conn.commit()
        except sqlite3.Error:
            conn.rollback(); raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(SCHEMA)
                existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
                for name, sql in (("rule", "ALTER TABLE tasks ADD COLUMN rule TEXT"), ("points", "ALTER TABLE tasks ADD COLUMN points REAL")):
                    if name not in existing:
                        conn.execute(sql)
        except sqlite3.Error as exc:
            logger.error("Database initialization failed: %s", exc)
            raise

    def save_task(self, record: TaskRecord) -> int:
        now = time.time()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO tasks (original_task,task_type,search_query,answer,location,rule,url,points,metadata,status,error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record.original_task, record.task_type, record.search_query, record.answer, record.location, record.rule, record.url, record.points, json.dumps(record.metadata or {}), record.status, record.error, now, now),
                )
                return int(cur.lastrowid)
        except sqlite3.Error as exc:
            logger.error("Failed to save task record: %s", exc); raise

    def update_task(self, task_id: int, **fields: Any) -> None:
        if not fields: return
        allowed = {"task_type","search_query","answer","location","rule","url","points","metadata","status","error"}
        parts=[]; values=[]
        for key,value in fields.items():
            if key not in allowed: raise ValueError(f"Cannot update unknown column: {key}")
            if key == "metadata": value=json.dumps(value or {})
            parts.append(f"{key} = ?"); values.append(value)
        parts.append("updated_at = ?"); values.extend([time.time(), task_id])
        with self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(parts)} WHERE id = ?", values)

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._connect() as conn:
            row=conn.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
            return TaskRecord.from_row(row) if row else None

    def find_similar(self, search_query: str | None, task_type: str | None = None, limit: int = 5, max_age_seconds: float | None = None) -> list[TaskRecord]:
        if not search_query: return []
        sql="SELECT * FROM tasks WHERE search_query LIKE ? AND status='success'"; params=[f"%{search_query}%"]
        if task_type: sql += " AND task_type = ?"; params.append(task_type)
        if max_age_seconds is not None:
            sql += " AND created_at >= ?"; params.append(time.time()-max_age_seconds)
        sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        with self._connect() as conn:
            return [TaskRecord.from_row(r) for r in conn.execute(sql,params).fetchall()]

    def is_fresh(self, record: TaskRecord, max_age_seconds: float) -> bool:
        return bool(record.created_at and (time.time()-record.created_at) <= max_age_seconds)

    def recent_tasks(self, limit: int = 20) -> list[TaskRecord]:
        with self._connect() as conn:
            return [TaskRecord.from_row(r) for r in conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()]

    def close(self) -> None:
        if self._persistent_conn is not None:
            self._persistent_conn.close(); self._persistent_conn=None

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()
