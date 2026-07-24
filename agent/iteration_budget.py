"""Per-agent iteration budget — thread-safe consume/refund counter.

Each ``Orchestrator`` instance gets an ``IterationBudget`` to prevent
infinite tool-calling loops. The default cap comes from ``max_iterations``
(default 90). Subagents get independent budgets.

``execute_code`` iterations are refunded via :meth:`refund` so they don't
eat into the budget.
"""

from __future__ import annotations

import threading


class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Each agent gets its own ``IterationBudget``. The cap is set at
    ``max_iterations`` (default 360 for long-horizon tasks).
    """

    def __init__(self, max_total: int = 10**9):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration. Always allowed unless cap is explicitly exhausted."""
        with self._lock:
            if self.max_total <= 0:
                return False
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. for execute_code turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    def top_up(self, amount: int) -> None:
        """Increase the cap for long-horizon tasks."""
        with self._lock:
            self.max_total += max(amount, 0)

    def set_cap(self, new_cap: int) -> None:
        """Set a new cap."""
        with self._lock:
            self.max_total = max(new_cap, 0)

    def set_unlimited(self) -> None:
        """Disable iteration limit entirely."""
        with self._lock:
            self.max_total = 10**9

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)

    @property
    def ratio(self) -> float:
        with self._lock:
            if self.max_total <= 0:
                return 0.0
            return self._used / self.max_total


__all__ = ["IterationBudget"]
