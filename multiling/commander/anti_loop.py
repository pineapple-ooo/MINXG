"""
anti_loop.py — Prompt-based anti-loop guardian for the Commander framework.

THE PROBLEM (from the spec)
===========================
"LLM陷入工具调用循环这玩意最难解决,要大量提示词或大量熔断才能解决
(不建议用熔断,要不然AI完全不知道怎么完成任务一直停一直停的)"

Translation: "LLM getting stuck in tool-call loops is the hardest problem
to solve — it needs either massive prompts or heavy breakers. Don't use
breakers: the AI just doesn't know how to complete the task and keeps
stalling forever."

THE SOLUTION
============
No hard breakers. Instead, three escalating prompt-based interventions:

1. TRAJECTORY HINT (softest)
   After N repeated tool calls with the same fingerprint, inject a
   short context line into the agent's next prompt:
   "[anti-loop] You've called <tool> 3× with similar args. Re-examine
   your approach — are you making progress or spinning?"

2. REFOCUS PROMPT (medium)
   After M repeated calls, inject a stronger prompt that forces the
   agent to explain its plan before continuing:
   "[anti-loop] 5 repeated calls detected. Before calling any more
   tools, state: (1) what you've accomplished, (2) what remains,
   (3) why the next tool call is necessary. If you can't answer #3,
   stop and deliver what you have."

3. FINAL SUMMONS (hardest, still not a breaker)
   After K repeated calls, inject the strongest prompt that tells
   the agent the session will wrap up after this iteration and it
   must produce a deliverable NOW:
   "[anti-loop] 8 repeated calls. This is your last opportunity to
   deliver a result. Summarize what you have and stop."

The key insight: we never BLOCK the tool call. We always let it
through. But we make the LLM progressively more aware that it's
spinning, and the prompts get more forceful. This matches the spec's
"大量提示词" (massive prompts) approach and avoids the "一直停一直停"
(keep stopping forever) failure mode of hard breakers.

Detection uses the same fingerprint approach as src/ai/safety/guard.py
— (tool_name, canonicalized_args_hash) — but we never return
(allowed=False). We always return (allowed=True) plus a LoopSignal
containing the prompt to inject.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


# ──────────────────────────────────────── tunables (env-overridable) ─────

import os

def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v and v.strip() else default
    except (TypeError, ValueError):
        return default


# Thresholds for each escalation tier. Override via env vars for
# CI / device-specific tuning.
HINT_THRESHOLD = _env_int("AgentHarness_LOOP_HINT_THRESHOLD", 2)
REFOCUS_THRESHOLD = _env_int("AgentHarness_LOOP_REFOCUS_THRESHOLD", 3)
SUMMONS_THRESHOLD = _env_int("AgentHarness_LOOP_SUMMONS_THRESHOLD", 5)


@dataclass
class LoopSignal:
    """The signal returned by LoopGuardian.pre_check().

    Attributes:
        should_inject: whether a prompt should be injected at all.
        injection: the prompt text to prepend to the agent's next
            tool result / next turn. Empty when should_inject is False.
        tier: 0 = none, 1 = hint, 2 = refocus, 3 = summons.
        repeated_count: how many times this fingerprint has been seen.
        tool_name: the tool being called repeatedly.
    """
    should_inject: bool = False
    injection: str = ""
    tier: int = 0
    repeated_count: int = 0
    tool_name: str = ""


def _fingerprint(name: str, args: Dict[str, Any]) -> str:
    """Canonical fingerprint of a tool call. Same approach as
    src/ai/safety/guard.py::DupDetector._fingerprint."""
    try:
        canonical = json.dumps(args, sort_keys=True,
                               default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = repr(sorted(args.items()))
    return hashlib.sha256(
        f"{name}|{canonical}".encode("utf-8")
    ).hexdigest()[:16]


class LoopGuardian:
    """Prompt-based anti-loop guardian.

    Unlike src/ai/safety/guard.py::AntiLoopGuard (which *blocks*
    duplicate calls), this guardian NEVER blocks. It always allows
    the call through and instead returns a LoopSignal containing an
    escalating prompt to inject into the agent's context.

    Rationale: the spec explicitly says breakers cause the AI to
    "keep stopping forever" because it doesn't know how to complete
    the task. Prompts keep the agent moving while making it aware
    it's spinning.
    """

    def __init__(self,
                 hint_threshold: int = HINT_THRESHOLD,
                 refocus_threshold: int = REFOCUS_THRESHOLD,
                 summons_threshold: int = SUMMONS_THRESHOLD):
        self.hint_threshold = max(2, hint_threshold)
        self.refocus_threshold = max(self.hint_threshold + 1,
                                      refocus_threshold)
        self.summons_threshold = max(self.refocus_threshold + 1,
                                      summons_threshold)
        # fingerprint -> count of consecutive repeats
        self._repeat_counts: Dict[str, int] = {}
        # rolling window of recent (fp, name, ts) for diagnostics
        self._recent: Deque[Tuple[str, str, float]] = deque(maxlen=64)
        self._lock = threading.Lock()

    def pre_check(self, name: str, args: Dict[str, Any]) -> Tuple[bool, LoopSignal]:
        """Always returns (True, signal). The signal tells the caller
        what prompt (if any) to inject. We NEVER block the call."""
        fp = _fingerprint(name, args)
        with self._lock:
            count = self._repeat_counts.get(fp, 0) + 1
            self._repeat_counts[fp] = count
            self._recent.append((fp, name, time.time()))

        signal = self._build_signal(count, name)
        return True, signal

    def record_success(self, name: str, args: Dict[str, Any]) -> None:
        """When a tool call produces a DIFFERENT result (progress),
        decay the repeat counter for that fingerprint. This lets the
        agent recover: if it calls the same tool but gets new output,
        it's not actually looping."""
        fp = _fingerprint(name, args)
        with self._lock:
            if fp in self._repeat_counts:
                self._repeat_counts[fp] = max(0,
                    self._repeat_counts[fp] - 2)

    def reset(self) -> None:
        """Clear all counters. Call at the start of a new agent turn."""
        with self._lock:
            self._repeat_counts.clear()
            self._recent.clear()

    def reset_fingerprint(self, name: str, args: Dict[str, Any]) -> None:
        """Clear the counter for a specific fingerprint — used when
        the agent successfully completes a step that used that tool."""
        fp = _fingerprint(name, args)
        with self._lock:
            self._repeat_counts.pop(fp, None)

    def _build_signal(self, count: int, name: str) -> LoopSignal:
        """Build the escalating prompt signal based on repeat count."""
        if count < self.hint_threshold:
            return LoopSignal(should_inject=False, repeated_count=count,
                              tool_name=name)

        if count < self.refocus_threshold:
            # Tier 1: gentle hint
            return LoopSignal(
                should_inject=True,
                tier=1,
                repeated_count=count,
                tool_name=name,
                injection=(
                    f"[anti-loop] You've called `{name}` {count}× with "
                    f"similar arguments. Re-examine: are you making real "
                    f"progress, or are you spinning? If the output isn't "
                    f"changing, your approach is wrong — try a different "
                    f"tool or step back and think."
                ),
            )

        if count < self.summons_threshold:
            # Tier 2: forced refocus
            return LoopSignal(
                should_inject=True,
                tier=2,
                repeated_count=count,
                tool_name=name,
                injection=(
                    f"[anti-loop] {count} repeated `{name}` calls detected. "
                    f"BEFORE calling any more tools, state explicitly:\n"
                    f"  (1) what you have accomplished so far,\n"
                    f"  (2) what remains to be done,\n"
                    f"  (3) why the next tool call is necessary.\n"
                    f"If you cannot answer (3), STOP calling tools and "
                    f"deliver the best result you currently have. A "
                    f"partial deliverable beats an infinite loop."
                ),
            )

        # Tier 3: final summons (still not a breaker — let the call
        # through, but tell the agent this is its last shot)
        return LoopSignal(
            should_inject=True,
            tier=3,
            repeated_count=count,
            tool_name=name,
            injection=(
                f"[anti-loop] {count} repeated `{name}` calls. This is "
                f"your final opportunity to deliver. Summarize what you "
                f"have accomplished, note any remaining gaps, and produce "
                f"a final result. Do NOT call `{name}` again — the loop "
                f"is not going to resolve by itself."
            ),
        )

    def snapshot(self) -> Dict[str, Any]:
        """Diagnostic snapshot for the Commander / debugging."""
        with self._lock:
            return {
                "repeat_counts": dict(self._repeat_counts),
                "recent_calls": [
                    {"fp": fp, "tool": name, "ts": ts}
                    for fp, name, ts in self._recent
                ],
                "thresholds": {
                    "hint": self.hint_threshold,
                    "refocus": self.refocus_threshold,
                    "summons": self.summons_threshold,
                },
            }
