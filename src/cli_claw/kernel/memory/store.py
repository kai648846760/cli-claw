from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryKey:
    channel: str
    chat_id: str
    date: str
    thread_id: str = "root"

    def to_string(self) -> str:
        return f"{self.channel}:{self.chat_id}:{self.date}:{self.thread_id or 'root'}"


class SQLiteMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jsonl_lock = threading.Lock()
        self._jsonl_path = self.path.with_suffix(".jsonl")
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        try:
            self._jsonl_path.touch(exist_ok=True)
        except Exception:
            pass

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_sessions (
                    key TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    summary TEXT,
                    provider TEXT,
                    updated_at REAL,
                    compression_count INTEGER DEFAULT 0,
                    metadata TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments TEXT,
                    provider TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_profile (
                    key TEXT PRIMARY KEY,
                    preferences TEXT,
                    rules TEXT,
                    updated_at REAL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_sessions_lookup ON memory_sessions(channel, chat_id, thread_id, date)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_messages_lookup ON memory_messages(channel, chat_id, thread_id, date, ts)")

    def make_key(
        self,
        channel: str,
        chat_id: str,
        *,
        date: str,
        thread_id: str | None = None,
    ) -> MemoryKey:
        return MemoryKey(channel=channel, chat_id=chat_id, date=date, thread_id=thread_id or "root")

    def get_latest(self, channel: str, chat_id: str, thread_id: str | None = None) -> dict[str, Any] | None:
        thread = thread_id or "root"
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT * FROM memory_sessions
                WHERE channel = ? AND chat_id = ? AND thread_id = ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (channel, chat_id, thread),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, key: MemoryKey) -> dict[str, Any] | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM memory_sessions WHERE key = ? LIMIT 1",
                (key.to_string(),),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def set(
        self,
        *,
        key: MemoryKey,
        summary: str,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_sessions (key, channel, chat_id, date, thread_id, summary, provider, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    summary = excluded.summary,
                    provider = excluded.provider,
                    updated_at = excluded.updated_at,
                    metadata = excluded.metadata
                """,
                (
                    key.to_string(),
                    key.channel,
                    key.chat_id,
                    key.date,
                    key.thread_id,
                    summary,
                    provider,
                    time.time(),
                    payload,
                ),
            )
        if summary:
            self._append_jsonl(
                {
                    "type": "summary",
                    "ts": time.time(),
                    "key": key.to_string(),
                    "channel": key.channel,
                    "chat_id": key.chat_id,
                    "date": key.date,
                    "thread_id": key.thread_id,
                    "summary": summary,
                    "provider": provider,
                    "metadata": metadata or {},
                }
            )

    def delete(self, key: MemoryKey) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM memory_sessions WHERE key = ?", (key.to_string(),))
            self._conn.execute("DELETE FROM memory_messages WHERE key = ?", (key.to_string(),))
        return cur.rowcount > 0

    def update_metadata(self, key: MemoryKey, updates: dict[str, Any]) -> bool:
        record = self.get(key)
        if not record:
            return False
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(updates)
        self.set(
            key=key,
            summary=record.get("summary") or "",
            provider=record.get("provider"),
            metadata=metadata,
        )
        return True

    def append_message(
        self,
        *,
        key: MemoryKey,
        role: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
        provider: str | None = None,
        ts: float | None = None,
    ) -> None:
        if not content:
            return
        payload = json.dumps(attachments or [], ensure_ascii=False)
        timestamp = ts or time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_messages
                (key, channel, chat_id, date, thread_id, ts, role, content, attachments, provider)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.to_string(),
                    key.channel,
                    key.chat_id,
                    key.date,
                    key.thread_id,
                    timestamp,
                    role,
                    content,
                    payload,
                    provider,
                ),
            )
        self._append_jsonl(
            {
                "type": "message",
                "ts": timestamp,
                "key": key.to_string(),
                "channel": key.channel,
                "chat_id": key.chat_id,
                "date": key.date,
                "thread_id": key.thread_id,
                "role": role,
                "content": content,
                "attachments": attachments or [],
                "provider": provider,
            }
        )

    def get_recent_messages(
        self,
        key: MemoryKey,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT role, content, attachments
                FROM memory_messages
                WHERE key = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (key.to_string(), limit),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in reversed(rows):
            attachments = []
            raw = row["attachments"]
            if raw:
                try:
                    attachments = json.loads(raw)
                except json.JSONDecodeError:
                    attachments = []
            messages.append({"role": row["role"], "content": row["content"], "attachments": attachments})
        return messages

    def _row_to_record(self, row: sqlite3.Row) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                metadata = {}
        return {
            "key": row["key"],
            "channel": row["channel"],
            "chat_id": row["chat_id"],
            "date": row["date"],
            "thread_id": row["thread_id"],
            "summary": row["summary"],
            "provider": row["provider"],
            "updated_at": row["updated_at"],
            "metadata": metadata,
        }

    def import_jsonl(self, path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                if data.get("type") == "message":
                    continue
                if "summary" not in data and data.get("type") not in {"summary", "session"}:
                    continue
            channel = data.get("channel")
            chat_id = data.get("chat_id")
            date_value = data.get("date")
            thread_id = data.get("thread_id") or "root"
            if not (isinstance(channel, str) and isinstance(chat_id, str) and isinstance(date_value, str)):
                continue
            summary = data.get("summary")
            if not isinstance(summary, str) or not summary:
                continue
            key = MemoryKey(channel=channel, chat_id=chat_id, date=date_value, thread_id=thread_id)
            self.set(
                key=key,
                summary=summary,
                provider=data.get("provider"),
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            )
            count += 1
        return count

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        try:
            with self._jsonl_lock:
                self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self._jsonl_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            return


MemoryStore = SQLiteMemoryStore
