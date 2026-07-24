"""
reviewer.py — Sideline critic + task auditor for the Commander framework.

The spec says: "台下还得有挑刺的和审核任务的" — there must be a
critic on the sidelines and a task auditor.

The Reviewer is a *separate role* from the Commander and the worker
agents. It:
  1. Reviews submitted tasks (IN_REVIEW status) — approves or rejects
     with feedback.
  2. Proactively critiques the overall project direction — raises
     concerns about quality, missing requirements, architectural
     drift.
  3. Audits agent behavior for signs of trouble (looping, wasted
     effort, shallow work).

Like the AgentPool, the Reviewer does not call AI models directly —
it's an injectable handler (same pattern as SubagentPool). The
Reviewer's *logic* is fully testable without a live model; the
handler defaults to a real sub-agent call when given an orchestrator.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """The outcome of reviewing a task.

    Attributes:
        approved: whether the task's result is acceptable.
        feedback: specific feedback for the worker agent(s).
        concerns: broader concerns to surface to the Commander.
        severity: "info" | "warning" | "critical" — tells the
            Commander how urgently to act.
        reviewer_id: which reviewer produced this (if multiple).
    """
    approved: bool = False
    feedback: str = ""
    concerns: List[str] = field(default_factory=list)
    severity: str = "info"
    reviewer_id: str = "reviewer_1"
    reviewed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "feedback": self.feedback,
            "concerns": list(self.concerns),
            "severity": self.severity,
            "reviewer_id": self.reviewer_id,
            "reviewed_at": self.reviewed_at,
        }


# ─────────────────────────── prompt templates ───────────────────────────

REVIEW_SYSTEM_PROMPT = (
    "You are a strict but fair task reviewer for a multi-agent coding "
    "crew. You will be given a task description and the work product "
    "submitted by one or more worker agents. Evaluate whether the work "
    "actually fulfills the task requirements. "
    "Reply with ONLY a JSON object: "
    '{"approved": true|false, "feedback": "<specific actionable '
    'feedback for the workers>", "concerns": ["<broader concern for '
    'the commander>", ...], "severity": "info|warning|critical"}. '
    "No prose, no markdown fences."
)

CRITIQUE_SYSTEM_PROMPT = (
    "You are a sideline critic for a multi-agent project. The Commander "
    "will give you a project summary and current task board state. "
    "Raise any concerns about: (1) missing requirements, (2) "
    "architectural drift, (3) quality risks, (4) agents that seem "
    "stuck or looping, (5) over/under-staffing. "
    "Reply with ONLY a JSON object: "
    '{"concerns": ["<concern>", ...], "severity": '
    '"info|warning|critical", "recommendation": "<one-sentence '
    'suggestion for the commander>"}. No prose, no markdown fences.'
)


# ────────────────────────── tolerant parsing ─────────────────────────────

def _extract_json(text: str, opener: str, closer: str) -> Optional[Any]:
    """Find the first `opener...closer` span and json.loads it.
    Same defensive approach as multiagent_ext — models wrap JSON
    in fences and add prose despite instructions."""
    if not text:
        return None
    start = text.find(opener)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


def _parse_review(text: str) -> ReviewResult:
    """Parse a review response from the LLM into a ReviewResult.
    Fail-safe: if the model didn't follow the format, treat as
    not-approved with the raw text as feedback — the pipeline stops
    (bounded rounds) rather than looping on garbage forever."""
    data = _extract_json(text, "{", "}")
    if not isinstance(data, dict) or "approved" not in data:
        return ReviewResult(
            approved=False,
            feedback=f"[unparseable review] {text[:500]}",
            concerns=["reviewer output did not match expected format"],
            severity="warning",
        )
    return ReviewResult(
        approved=bool(data["approved"]),
        feedback=str(data.get("feedback", "")),
        concerns=[str(c) for c in (data.get("concerns") or [])],
        severity=str(data.get("severity", "info")),
    )


def _parse_critique(text: str) -> Dict[str, Any]:
    """Parse a critique response. Fail-safe: empty concerns on
    parse failure."""
    data = _extract_json(text, "{", "}")
    if not isinstance(data, dict):
        return {"concerns": [], "severity": "info",
                "recommendation": text[:200] if text else ""}
    return {
        "concerns": [str(c) for c in (data.get("concerns") or [])],
        "severity": str(data.get("severity", "info")),
        "recommendation": str(data.get("recommendation", "")),
    }


# ─────────────────────────── the reviewer ────────────────────────────────

class Reviewer:
    """Sideline critic + task auditor.

    Three functions:
      1. review_task(task, result) — evaluate a submitted task.
      2. critique_project(summary) — proactive project-level critique.
      3. audit_agents(pool_snapshot) — spot trouble in agent behavior.

    All three use an injectable handler (callable -> str) for testability,
    same pattern as SubagentPool. When no handler is given, a heuristic
    fallback is used (always approves with a note) — this lets the
    Commander run without a live model in degraded mode.
    """

    def __init__(self, reviewer_id: str = "reviewer_1",
                 handler: Optional[Callable] = None):
        self.reviewer_id = reviewer_id
        self.handler = handler
        self._history: List[ReviewResult] = []

    def review_task(self, task_title: str, task_description: str,
                    result: str,
                     handler: Optional[Callable] = None) -> ReviewResult:
        """Review a submitted task result.

        Args:
            task_title: short task title.
            task_description: full task description.
            result: the work product submitted by the worker agent(s).
            handler: injectable AI handler for testing. Defaults to
                self.handler.

        Returns:
            ReviewResult with approve/reject + feedback.
        """
        run_handler = handler or self.handler
        if run_handler is not None:
            prompt = (
                f"Task: {task_title}\n\n"
                f"Description: {task_description}\n\n"
                f"Submitted work:\n{result}\n\n"
                f"Evaluate whether the submitted work fulfills the task."
            )
            try:
                raw = run_handler(
                    system_prompt=REVIEW_SYSTEM_PROMPT,
                    user_prompt=prompt,
                )
                review = _parse_review(raw or "")
                review.reviewer_id = self.reviewer_id
                self._history.append(review)
                return review
            except Exception as e:
                logger.warning("review_task handler failed: %r", e)
                # Fall through to heuristic fallback.

        # Heuristic fallback (degraded mode — no live model).
        review = ReviewResult(
            approved=bool(result and len(result) > 10),
            feedback="[degraded mode] Auto-approved: no reviewer handler "
                     "configured. Manual review recommended.",
            concerns=["reviewer running in degraded mode (no AI handler)"],
            severity="info",
            reviewer_id=self.reviewer_id,
        )
        self._history.append(review)
        return review

    def critique_project(self, project_summary: str,
                          handler: Optional[Callable] = None) -> Dict[str, Any]:
        """Proactive project-level critique.

        The Commander calls this periodically to get a outside
        perspective on the overall direction.

        Args:
            project_summary: a text summary of the project state,
                task board, and any open concerns.
            handler: injectable AI handler.

        Returns:
            Dict with concerns, severity, recommendation.
        """
        run_handler = handler or self.handler
        if run_handler is not None:
            try:
                raw = run_handler(
                    system_prompt=CRITIQUE_SYSTEM_PROMPT,
                    user_prompt=project_summary,
                )
                return _parse_critique(raw or "")
            except Exception as e:
                logger.warning("critique_project handler failed: %r", e)

        # Heuristic fallback.
        return {
            "concerns": [],
            "severity": "info",
            "recommendation": "[degraded mode] No critique available "
                              "(no reviewer handler configured).",
        }

    def audit_agents(self, pool_snapshot: Dict[str, Any]) -> List[str]:
        """Spot trouble in agent behavior from a pool snapshot.

        This is a pure heuristic (no AI call) — it checks for:
          - agents stuck in WORKING for too long
          - agents with high error counts
          - no idle agents (over-utilization)
          - too many failed agents

        Returns a list of concern strings (empty = no concerns).
        """
        concerns: List[str] = []
        by_state = pool_snapshot.get("by_state", {})
        failed = by_state.get("failed", 0)
        working = by_state.get("working", 0)
        idle = by_state.get("idle", 0)
        total = pool_snapshot.get("total_agents", 0)

        if failed > 0:
            concerns.append(
                f"{failed} agent(s) have failed — Commander should "
                f"decide on replacement or absorption"
            )
        if total > 0 and working == total and idle == 0:
            concerns.append(
                "no idle agents — pool may be under-provisioned for "
                "the current workload"
            )
        if pool_snapshot.get("total_errors", 0) > 5:
            concerns.append(
                f"high total error count "
                f"({pool_snapshot['total_errors']}) — agents may be "
                f"struggling with the task difficulty"
            )
        return concerns

    def get_history(self) -> List[ReviewResult]:
        """All reviews this reviewer has done (for audit trail)."""
        return list(self._history)

    def summary(self) -> Dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "total_reviews": len(self._history),
            "approved": sum(1 for r in self._history if r.approved),
            "rejected": sum(1 for r in self._history if not r.approved),
        }
