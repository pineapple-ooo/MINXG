"""minxg.context.memory — day-scale memory hierarchy for long-running agents.

Memory layers
-------------
* **Working Memory**: the active conversation context (last N messages).
* **Episodic Memory**: compressed summaries of past sessions (tier-1/2/3).
* **Semantic Memory**: distilled facts extracted from sessions (entity graph).
* **Procedural Memory**: reusable tool-use patterns learned from past sessions.

All layers are persisted to a local store (SQLite by default) and can be
exported / imported for agent migration.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import zlib
import base64
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from minxg.context.compression import estimate_tokens, compress, decompress

logger = logging.getLogger(__name__)

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    tier TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    predicate TEXT NOT NULL,
    obj TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    source_session TEXT
);
CREATE TABLE IF NOT EXISTS memory_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_hash TEXT NOT NULL UNIQUE,
    pattern_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    uses INTEGER NOT NULL DEFAULT 1,
    last_used REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON memory_episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON memory_facts(entity);
"""


@dataclass
class Episode:
    """A compressed episode of past conversation."""
    id: Optional[int] = None
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    tier: str = "high"
    token_count: int = 0
    data: str = ""  # JSON-serialised compressed messages

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "tier": self.tier,
            "token_count": self.token_count,
            "data": self.data,
        }


class MemoryStore:
    """Persistent memory backend backed by SQLite."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DB_SCHEMA)
        self._conn.commit()

    def add_episode(self, episode: Episode) -> int:
        cur = self._conn.execute(
            "INSERT INTO memory_episodes (session_id, created_at, tier, token_count, data) VALUES (?, ?, ?, ?, ?)",
            (episode.session_id, episode.created_at, episode.tier, episode.token_count, episode.data),
        )
        self._conn.commit()
        episode.id = cur.lastrowid
        return episode.id

    def get_episodes(self, session_id: str, limit: int = 50) -> List[Episode]:
        cur = self._conn.execute(
            "SELECT * FROM memory_episodes WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [Episode(**dict(row)) for row in cur.fetchall()]

    def add_fact(self, entity: str, predicate: str, obj: str, confidence: float = 1.0, source_session: str = "") -> None:
        self._conn.execute(
            "INSERT INTO memory_facts (entity, predicate, obj, confidence, created_at, source_session) VALUES (?, ?, ?, ?, ?, ?)",
            (entity, predicate, obj, confidence, time.time(), source_session),
        )
        self._conn.commit()

    def query_facts(self, entity: Optional[str] = None, predicate: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM memory_facts WHERE 1=1"
        params: List[Any] = []
        if entity is not None:
            q += " AND entity = ?"
            params.append(entity)
        if predicate is not None:
            q += " AND predicate = ?"
            params.append(predicate)
        cur = self._conn.execute(q, params)
        return [dict(row) for row in cur.fetchall()]

    def add_pattern(self, pattern_type: str, payload: Dict[str, Any]) -> None:
        payload_str = json.dumps(payload, ensure_ascii=False)
        pattern_hash = base64.b64encode(
            hashlib.sha256(payload_str.encode()).digest()
        ).decode("ascii")[:16]
        try:
            self._conn.execute(
                "INSERT INTO memory_patterns (pattern_hash, pattern_type, payload, uses, last_used) VALUES (?, ?, ?, 1, ?)",
                (pattern_hash, pattern_type, payload_str, time.time()),
            )
        except sqlite3.IntegrityError:
            self._conn.execute(
                "UPDATE memory_patterns SET uses = uses + 1, last_used = ? WHERE pattern_hash = ?",
                (time.time(), pattern_hash),
            )
        self._conn.commit()

    def get_patterns(self, pattern_type: str, limit: int = 20) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM memory_patterns WHERE pattern_type = ? ORDER BY uses DESC, last_used DESC LIMIT ?",
            (pattern_type, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


import hashlib  # noqa: E402 — late import for sqlite3 path


class DayMemory:
    """High-level day-scale memory manager.

    Usage
    -----
    >>> mem = DayMemory()
    >>> mem.ingest(session_id, messages)
    >>> facts = mem.recall_facts(entity="user")
    """

    def __init__(self, store: Optional[MemoryStore] = None) -> None:
        self.store = store or MemoryStore()

    def ingest(self, session_id: str, messages: List[Dict[str, Any]]) -> Episode:
        """Compress and store a session transcript."""
        ctx = compress(messages, tier="high")
        data = json.dumps(ctx.to_dict(), ensure_ascii=False)
        episode = Episode(
            session_id=session_id,
            tier=ctx.tier,
            token_count=ctx.compressed_tokens,
            data=data,
        )
        self.store.add_episode(episode)
        logger.debug("Ingested session %s: %d msgs -> %d tokens (tier=%s)", session_id, len(messages), ctx.compressed_tokens, ctx.tier)
        return episode

    def recall(self, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Return recent episodes for ``session_id`` as reconstructed messages."""
        episodes = self.store.get_episodes(session_id, limit=limit)
        results: List[Dict[str, Any]] = []
        for ep in episodes:
            try:
                ctx_data = json.loads(ep.data)
                ctx = CompressedContext(**ctx_data)
                msgs = decompress(ctx)
                results.append({"episode": ep.to_dict(), "messages": msgs})
            except Exception as exc:
                logger.debug("Failed to reconstruct episode %s: %s", ep.id, exc)
        return results

    def distill_facts(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Extract simple entity-predicate-object facts from messages."""
        # Lightweight heuristic extractor: look for "X is Y", "X has Y", etc.
        patterns = re.compile(
            r"(?P<entity>[A-Z][a-zA-Z]+)\s+(?:is|has|needs|wants|prefers)\s+(?P<obj>.{3,80})"
        )
        for m in messages:
            content = m.get("content", "")
            if not isinstance(content, str):
                continue
            for match in patterns.finditer(content):
                entity = match.group("entity")
                predicate = m.get("role", "mention")
                obj = match.group("obj").rstrip(".")
                self.store.add_fact(entity, predicate, obj, source_session=session_id)

    def recall_facts(self, entity: Optional[str] = None, predicate: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query the fact store."""
        return self.store.query_facts(entity=entity, predicate=predicate)

    def close(self) -> None:
        self.store.close()

