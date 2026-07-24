"""agent_harness.context — long-running agent context management.

Public surface
--------------
* :mod:`agent_harness.context.compression` — multi-agent 3-tier auto compression
* :mod:`agent_harness.context.memory` — day-scale memory hierarchy
* :mod:`agent_harness.context.token_tracker` — token accounting
* :mod:`agent_harness.context.model_probe` — dynamic model context-window detection
* :mod:`agent_harness.context.dev_utils` — developer utilities: savepoint, diff, restore
"""
from __future__ import annotations

from .compression import (
    AutoCompressor,
    CompressedContext,
    compress,
    compress_for_budget,
    decompress,
    detect_context_window,
    estimate_tokens,
    usage_ratio,
)
from .memory import DayMemory
from .model_probe import ModelContextProbe
from .token_tracker import TokenBudgetTracker

try:
    from .dev_utils import SavepointManager, diff_messages, restore_savepoint
except ImportError:  # pragma: no cover
    SavepointManager = None  # type: ignore[misc,assignment]
    diff_messages = None  # type: ignore[misc,assignment]
    restore_savepoint = None  # type: ignore[misc,assignment]

__all__ = [
    "AutoCompressor",
    "CompressedContext",
    "DayMemory",
    "ModelContextProbe",
    "SavepointManager",
    "TokenBudgetTracker",
    "compress",
    "compress_for_budget",
    "decompress",
    "detect_context_window",
    "diff_messages",
    "estimate_tokens",
    "restore_savepoint",
    "usage_ratio",
]
