"""Conversation history manager for the TUI."""

from __future__ import annotations

from typing import Any, Dict, List


class History:
    def __init__(self) -> None:
        self._turns: List[Dict[str, Any]] = []

    def append_turn(self, user: str, assistant: str) -> None:
        self._turns.append({"user": user, "assistant": assistant})

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        return self._turns[-n:]
