"""
multiling/session_store.py — SQLite + FTS5 session/message store.

Replaces the per-session JSON file used by InfiniteContextManager with a
single WAL-mode SQLite database that supports:

  - concurrent readers + one writer
  - FTS5 full-text search over message content
  - atomic writes via WAL + explicit commit
  - session metadata (created_at, updated_at, message_count)
  - semantic facts as a JSON blob per session

Schema
------
sessions(
    session_id TEXT PRIMARY KEY,
    created_at REAL,
    updated_at REAL,
    message_count INTEGER,
    semantic_facts TEXT  -- JSON blob
)

messages(
    rowid INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL,
    metadata TEXT,       -- JSON blob
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)

messages_fts USING fts5(
    turn_id, role, content,
    content=sessions, content_rowid=rowid
)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


_DB_FILENAME = "sessions.sqlite"
_DEFAULT_DIR = Path.home() / ".agent_harness" / "sessions"


class SessionStore:
    """Thread-safe, async-friendly SQLite session store."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DIR / _DB_FILENAME
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), isolation_level="DEFERRED")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                semantic_facts TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                rowid INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                turn_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, timestamp);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                turn_id, role, content,
                content='messages', content_rowid='rowid'
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, turn_id, role, content)
                VALUES (NEW.rowid, NEW.turn_id, NEW.role, NEW.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, turn_id, role, content)
                VALUES ('delete', OLD.rowid, OLD.turn_id, OLD.role, OLD.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, turn_id, role, content)
                VALUES ('delete', OLD.rowid, OLD.turn_id, OLD.role, OLD.content);
                INSERT INTO messages_fts(rowid, turn_id, role, content)
                VALUES (NEW.rowid, NEW.turn_id, NEW.role, NEW.content);
            END;
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # session helpers
    # ------------------------------------------------------------------

    def ensure_session(self, session_id: str) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO sessions(session_id, created_at, updated_at, message_count, semantic_facts)
            VALUES (?, ?, ?, 0, '{}')
            """,
            (session_id, now, now),
        )
        self._conn.commit()

    def touch_session(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (time.time(), session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # message CRUD
    # ------------------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        turn_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """Insert a message and return its rowid."""
        ts = timestamp if timestamp is not None else time.time()
        tid = turn_id or f"turn_{ts:.0f}"
        meta = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        cur = self._conn.execute(
            """
            INSERT INTO messages(session_id, turn_id, role, content, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, tid, role, content, ts, meta),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ?, message_count = message_count + 1 WHERE session_id = ?",
            (ts, session_id),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        after_timestamp: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Return messages ordered by time ascending."""
        sql = "SELECT turn_id, role, content, timestamp, metadata FROM messages WHERE session_id = ?"
        params: List[Any] = [session_id]
        if after_timestamp is not None:
            sql += " AND timestamp > ?"
            params.append(after_timestamp)
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row["turn_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                }
            )
        return out

    def get_last_n(self, session_id: str, n: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT turn_id, role, content, timestamp, metadata
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (session_id, n),
        ).fetchall()
        return list(reversed([self._row_to_msg(r) for r in rows]))

    def delete_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # semantic memory
    # ------------------------------------------------------------------

    def load_semantic_facts(self, session_id: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT semantic_facts FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["semantic_facts"])
        except json.JSONDecodeError:
            return {}

    def save_semantic_facts(self, session_id: str, facts: Dict[str, Any]) -> None:
        blob = json.dumps(facts, ensure_ascii=False, default=str)
        self._conn.execute(
            "UPDATE sessions SET semantic_facts = ? WHERE session_id = ?",
            (blob, session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # FTS search
    # ------------------------------------------------------------------

    def search(self, session_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Full-text search inside a single session."""
        rows = self._conn.execute(
            """
            SELECT m.turn_id, m.role, m.content, m.timestamp, m.metadata,
                   rank
            FROM messages_fts f
            JOIN messages m ON m.rowid = f.rowid
            WHERE messages_fts MATCH ? AND m.session_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, session_id, limit),
        ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    # ------------------------------------------------------------------
    # stats / lifecycle
    # ------------------------------------------------------------------

    def session_stats(self, session_id: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return {}
        return dict(row)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["turn_id"],
            "role": row["role"],
            "content": row["content"],
            "timestamp": row["timestamp"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    # context-manager support
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
