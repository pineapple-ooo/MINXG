"""minxg.context.token_tracker — token accounting for web dashboard.

Separates input and output tokens and exposes Prometheus metrics for the
gateway / web UI to scrape.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TokenTracker:
    """Accumulate input/output token counters per conversation."""

    def __init__(self) -> None:
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._compressed_input_tokens: int = 0
        self._turns: int = 0
        self._last_tier: str = "max"

    def record_turn(
        self,
        input_tokens: int,
        output_tokens: int,
        compressed_input_tokens: int = 0,
        tier: str = "max",
    ) -> Dict[str, int]:
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._compressed_input_tokens += compressed_input_tokens
        self._turns += 1
        self._last_tier = tier
        return self.snapshot()

    def snapshot(self) -> Dict[str, int]:
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "compressed_input_tokens": self._compressed_input_tokens,
            "saved_tokens": self._input_tokens - self._compressed_input_tokens,
            "turns": self._turns,
            "last_tier": self._last_tier,
        }

    def reset(self) -> None:
        self._input_tokens = 0
        self._output_tokens = 0
        self._compressed_input_tokens = 0
        self._turns = 0
        self._last_tier = "max"


TokenBudgetTracker = TokenTracker
