"""agent_harness.context.dev_utils — developer utilities for message state.

* SavepointManager: save/restore message snapshots with diffs
* diff_messages: compute minimal edit script between two message lists
* restore_savepoint: rollback to a saved checkpoint
"""
from __future__ import annotations

import copy
import difflib
import json
import logging
import time
import zlib
import base64
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Savepoint:
    """A compressed snapshot of message state at a point in time."""
    id: str
    created_at: float = field(default_factory=time.time)
    message_count: int = 0
    token_count: int = 0
    description: str = ""
    data: str = ""  # compressed JSON
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "message_count": self.message_count,
            "token_count": self.token_count,
            "description": self.description,
            "metadata": self.metadata,
        }


class SavepointManager:
    """Manage savepoints for conversation/message state rollback."""

    def __init__(self, max_savepoints: int = 100) -> None:
        self.max_savepoints = max_savepoints
        self._savepoints: Dict[str, Savepoint] = {}
        self._order: List[str] = []

    def save(self, messages: List[Dict[str, Any]], description: str = "",
             metadata: Optional[Dict[str, Any]] = None) -> Savepoint:
        """Create a compressed savepoint of current message state."""
        from agent_harness.context.compression import estimate_tokens

        sp_id = f"sp_{int(time.time() * 1000)}_{len(self._order)}"
        raw = json.dumps(messages, ensure_ascii=False).encode("utf-8")
        compressed = base64.b64encode(zlib.compress(raw)).decode("ascii")
        token_count = estimate_tokens(messages)

        sp = Savepoint(
            id=sp_id,
            message_count=len(messages),
            token_count=token_count,
            description=description,
            data=compressed,
            metadata=metadata or {},
        )

        self._savepoints[sp_id] = sp
        self._order.append(sp_id)

        # Evict oldest if over limit
        while len(self._order) > self.max_savepoints:
            old = self._order.pop(0)
            self._savepoints.pop(old, None)

        logger.debug("Saved savepoint %s: %d msgs, %d tokens", sp_id, len(messages), token_count)
        return sp

    def restore(self, sp_id: str) -> List[Dict[str, Any]]:
        """Restore messages from a savepoint."""
        sp = self._savepoints.get(sp_id)
        if not sp:
            raise KeyError(f"Savepoint {sp_id} not found")
        raw = zlib.decompress(base64.b64decode(sp.data))
        return json.loads(raw.decode("utf-8"))

    def list_savepoints(self) -> List[Dict[str, Any]]:
        """List all available savepoints."""
        return [self._savepoints[sp_id].to_dict() for sp_id in self._order]

    def diff(self, sp_id_a: str, sp_id_b: str) -> str:
        """Show textual diff between two savepoints."""
        msgs_a = self.restore(sp_id_a)
        msgs_b = self.restore(sp_id_b)
        return diff_messages(msgs_a, msgs_b)

    def prune(self, keep_last: int = 50) -> int:
        """Remove old savepoints, keep only the most recent N."""
        if keep_last >= len(self._order):
            return 0
        to_remove = self._order[:-keep_last]
        for sp_id in to_remove:
            self._savepoints.pop(sp_id, None)
        self._order = self._order[-keep_last:]
        return len(to_remove)


def diff_messages(a: List[Dict[str, Any]], b: List[Dict[str, Any]],
                  context_lines: int = 3) -> str:
    """Compute unified diff between two message lists."""
    # Normalize to JSON strings for diffing
    lines_a = [json.dumps(m, ensure_ascii=False, sort_keys=True) for m in a]
    lines_b = [json.dumps(m, ensure_ascii=False, sort_keys=True) for m in b]
    diff = difflib.unified_diff(lines_a, lines_b, lineterm="",
                                 n=context_lines,
                                 fromfile="messages_a",
                                 tofile="messages_b")
    return "\n".join(diff) or "(no diff)"


def restore_savepoint(messages: List[Dict[str, Any]],
                      sp_id: str,
                      manager: SavepointManager) -> List[Dict[str, Any]]:
    """Convenience wrapper to restore a savepoint into the current list."""
    restored = manager.restore(sp_id)
    logger.info("Restored savepoint %s: %d messages", sp_id, len(restored))
    return restored


# ---------------------------------------------------------------------------
# Developer utilities
# ---------------------------------------------------------------------------

class DevToolbox:
    """Convenience wrapper for common dev operations."""

    def __init__(self, savepoint_manager: Optional[SavepointManager] = None) -> None:
        self.sp = savepoint_manager or SavepointManager()

    def snapshot(self, messages: List[Dict[str, Any]], label: str = "") -> Savepoint:
        """Save current state."""
        return self.sp.save(messages, description=label or f"auto_{int(time.time())}")

    def undo(self, messages: List[Dict[str, Any]], steps: int = 1) -> List[Dict[str, Any]]:
        """Rollback to an earlier savepoint."""
        if len(self.sp._order) < steps + 1:
            raise ValueError(f"Not enough savepoints to undo {steps} steps")
        target = self.sp._order[-(steps + 1)]
        return self.sp.restore(target)

    def diff_last(self, messages: List[Dict[str, Any]]) -> str:
        """Diff current messages against last savepoint."""
        if len(self.sp._order) < 2:
            return "(need at least 2 savepoints)"
        return self.sp.diff(self.sp._order[-2], self.sp._order[-1])

    def inspect(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a compact inspection report."""
        from agent_harness.context.compression import estimate_tokens
        roles: Dict[str, int] = {}
        tools: List[str] = []
        for m in messages:
            role = m.get("role", "unknown")
            roles[role] = roles.get(role, 0) + 1
            if role == "tool":
                name = m.get("name", "")
                if name and name not in tools:
                    tools.append(name)
        return {
            "count": len(messages),
            "tokens": estimate_tokens(messages),
            "roles": roles,
            "tools": tools[:50],
            "savepoints": len(self.sp._order),
        }
