"""Maps a conversation key to the Claude session that holds that conversation."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            " key TEXT PRIMARY KEY,"
            " session_id TEXT NOT NULL,"
            " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    def get(self, key: str) -> str | None:
        row = self._db.execute("SELECT session_id FROM sessions WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, session_id: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO sessions (key, session_id) VALUES (?, ?)", (key, session_id)
        )

    def close(self) -> None:
        self._db.close()
