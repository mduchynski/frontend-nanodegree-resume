"""SQLite-backed persistence: conversation history, long-term facts, spend.

Deliberately boring. One file, no server, no ORM -- this runs on a laptop.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .config import cfg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,   -- JSON-encoded content blocks
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);

CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'general',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    usd           REAL    NOT NULL,
    created_at    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_time ON spend(created_at);
"""


class Store:
    def __init__(self, path=None):
        self._path = str(path or cfg.db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------- conversation ----------------

    def append_turn(self, session_id: str, role: str, content: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?,?,?,?)",
                (session_id, role, json.dumps(content, default=str), time.time()),
            )
            self._conn.commit()

    def history(self, session_id: str, limit: int = 40) -> list[dict]:
        """Most recent `limit` turns, oldest first, in Messages-API shape."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        msgs = [{"role": r["role"], "content": json.loads(r["content"])} for r in reversed(rows)]
        return _repair(msgs)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            self._conn.commit()

    # ---------------- long-term facts ----------------

    def remember(self, key: str, value: str, category: str = "general") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts (key, value, category, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "category=excluded.category, updated_at=excluded.updated_at",
                (key.strip().lower(), value, category, time.time()),
            )
            self._conn.commit()

    def forget(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM facts WHERE key=?", (key.strip().lower(),))
            self._conn.commit()
            return cur.rowcount > 0

    def recall(self, query: str | None = None) -> list[dict]:
        with self._lock:
            if query:
                like = f"%{query.strip().lower()}%"
                rows = self._conn.execute(
                    "SELECT key, value, category FROM facts "
                    "WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT 40",
                    (like, like),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT key, value, category FROM facts ORDER BY updated_at DESC LIMIT 40"
                ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- spend ----------------

    def record_spend(self, model: str, usage: Any, usd: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend (model, input_tokens, output_tokens, cache_read, "
                "cache_write, usd, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    model,
                    getattr(usage, "input_tokens", 0) or 0,
                    getattr(usage, "output_tokens", 0) or 0,
                    getattr(usage, "cache_read_input_tokens", 0) or 0,
                    getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    usd,
                    time.time(),
                ),
            )
            self._conn.commit()

    def spend_this_month(self) -> float:
        start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(usd), 0.0) AS total FROM spend WHERE created_at >= ?",
                (start,),
            ).fetchone()
        return float(row["total"])

    def spend_breakdown(self) -> list[dict]:
        start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with self._lock:
            rows = self._conn.execute(
                "SELECT model, COUNT(*) AS calls, SUM(usd) AS usd, "
                "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                "SUM(cache_read) AS cache_read "
                "FROM spend WHERE created_at >= ? GROUP BY model ORDER BY usd DESC",
                (start,),
            ).fetchall()
        return [dict(r) for r in rows]


def _repair(msgs: list[dict]) -> list[dict]:
    """Make a truncated history legal for the Messages API.

    Trimming to the last N turns can slice between a tool_use and its
    tool_result, or leave the window starting on an assistant turn. Both are
    400s. Drop from the front until the history is well-formed.
    """
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)

    # A leading user turn made only of tool_result blocks is an orphan: its
    # matching tool_use was trimmed away.
    while msgs and _is_only_tool_results(msgs[0]["content"]):
        msgs.pop(0)
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)

    # A trailing assistant turn with unanswered tool_use blocks is also illegal.
    while msgs and msgs[-1]["role"] == "assistant" and _has_tool_use(msgs[-1]["content"]):
        msgs.pop()

    return msgs


def _is_only_tool_results(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _has_tool_use(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


store = Store()
