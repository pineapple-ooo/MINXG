"""
agent_harness.contracts.agent.temporal — Temporal Planning & Knowledge Distillation
===========================================================================

Bold design: agents that plan over time and distill knowledge into
compressed, transferable representations.

Temporal Planning
-----------------
1. **TemporalTask** — tasks with start/end constraints and durations
2. **TemporalPlan** — schedule satisfying temporal logic (STN/STNU)
3. **Resource Reservation** — book resources over time intervals
4. **Contingency Planning** — branch on uncertain outcomes

Knowledge Distillation
----------------------
1. **ExperienceBuffer** — prioritized replay of agent episodes
2. **SkillExtractor** — mine reusable skills from successful traces
3. **PolicyCompression** — distill policy into compact rules
4. **TransferLearning** — apply skills from one domain to another
"""
from __future__ import annotations

import heapq
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


__all__ = [
"TemporalTask",
"TemporalPlan",
"ResourceReservation",
"ContingencyPlan",
"ExperienceBuffer",
"Skill",
"PolicyCompressor",
"TransferLearner",
]

# ---------------------------------------------------------------------------
# Temporal Planning
# ---------------------------------------------------------------------------

@dataclass(order=True)
class TemporalTask:
    task_id: str
    name: str
    duration_s: float
    earliest_start: float = 0.0
    latest_finish: float = float("inf")
    dependencies: List[str] = field(default_factory=list, compare=False)
    resources: List[str] = field(default_factory=list, compare=False)
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)

@dataclass
class TemporalPlan:
    tasks: List[TemporalTask]
    reservations: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)

    def schedule(self) -> List[Tuple[str, float, float]]:
        earliest = {t.task_id: t.earliest_start for t in self.tasks}
        task_map = {t.task_id: t for t in self.tasks}
        order = sorted(self.tasks, key=lambda t: earliest[t.task_id])
        schedule = []
        for task in order:
            start = earliest[task.task_id]
            for dep in task.dependencies:
                dep_end = earliest[dep] + task_map[dep].duration_s
                start = max(start, dep_end)
            earliest[task.task_id] = start
            finish = start + task.duration_s
            schedule.append((task.task_id, start, finish))
            for res in task.resources:
                self.reservations.setdefault(res, []).append((start, finish))
        return schedule

    def check_feasibility(self) -> bool:
        schedule = self.schedule()
        for task_id, start, finish in schedule:
            task = next(t for t in self.tasks if t.task_id == task_id)
            if finish > task.latest_finish:
                return False
        return True

@dataclass
class ResourceReservation:
    resource_id: str
    intervals: List[Tuple[float, float]] = field(default_factory=list)

    def overlaps(self, start: float, finish: float) -> bool:
        return any(not (finish <= s or start >= f) for s, f in self.intervals)

    def reserve(self, start: float, finish: float) -> bool:
        if self.overlaps(start, finish):
            return False
        self.intervals.append((start, finish))
        self.intervals.sort()
        return True

@dataclass
class ContingencyPlan:
    main_plan: TemporalPlan
    branches: Dict[str, TemporalPlan] = field(default_factory=dict)
    trigger_conditions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def select_branch(self, condition: str) -> Optional[TemporalPlan]:
        if condition in self.branches:
            return self.branches[condition]
        return self.main_plan

# ---------------------------------------------------------------------------
# Knowledge Distillation
# ---------------------------------------------------------------------------

@dataclass
class ExperienceBuffer:
    """Prioritized replay buffer for agent experiences."""
    capacity: int = 1000
    _buffer: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _priorities: List[float] = field(default_factory=list, repr=False)

    def add(self, experience: Dict[str, Any], priority: float = 1.0) -> None:
        if len(self._buffer) >= self.capacity:
            idx = self._priorities.index(min(self._priorities))
            self._buffer[idx] = experience
            self._priorities[idx] = priority
        else:
            self._buffer.append(experience)
            self._priorities.append(priority)

    def sample(self, n: int = 10) -> List[Dict[str, Any]]:
        if not self._buffer:
            return []
        weights = [max(0.01, p) for p in self._priorities]
        total = sum(weights)
        probs = [w / total for w in weights]
        indices = sorted(range(len(self._buffer)), key=lambda i: probs[i], reverse=True)[:n]
        return [self._buffer[i] for i in indices]

    def __len__(self) -> int:
        return len(self._buffer)

@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    trigger_conditions: List[str]
    action_sequence: List[Dict[str, Any]]
    success_rate: float = 0.0
    usage_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PolicyCompressor:
    """Distill agent policy into compact, interpretable rules."""

    def compress(self, experiences: List[Dict[str, Any]]) -> List[Skill]:
        skills = []
        clusters: Dict[str, List[Dict[str, Any]]] = {}
        for exp in experiences:
            key = exp.get("tool", "unknown")
            clusters.setdefault(key, []).append(exp)
        for tool, exps in clusters.items():
            successes = [e for e in exps if e.get("outcome") == "success"]
            if not successes:
                continue
            skill = Skill(
                skill_id=hashlib.sha256(tool.encode()).hexdigest()[:12],
                name=f"skill_{tool}",
                description=f"Learned skill for {tool}",
                trigger_conditions=[tool],
                action_sequence=[{"tool": tool, "params": s.get("params", {})} for s in successes[:5]],
                success_rate=len(successes) / len(exps),
                usage_count=len(exps),
            )
            skills.append(skill)
        return skills

@dataclass
class TransferLearner:
    """Transfer skills across domains."""

    def can_transfer(self, skill: Skill, target_domain: str) -> float:
        domain_keywords = {
            "web": ["http", "fetch", "parse", "render"],
            "data": ["csv", "json", "transform", "aggregate"],
            "system": ["exec", "file", "process", "shell"],
        }
        target_keywords = domain_keywords.get(target_domain, [])
        if not target_keywords:
            return 0.0
        overlap = sum(1 for kw in target_keywords if kw in skill.description.lower())
        return overlap / len(target_keywords)

    def transfer(self, skill: Skill, target_domain: str) -> Optional[Skill]:
        score = self.can_transfer(skill, target_domain)
        if score < 0.3:
            return None
        return Skill(
            skill_id=f"{skill.skill_id}_xfer_{target_domain}",
            name=f"{skill.name}__xfer_{target_domain}",
            description=f"Transferred: {skill.description}",
            trigger_conditions=skill.trigger_conditions + [target_domain],
            action_sequence=skill.action_sequence,
            success_rate=skill.success_rate * score,
            usage_count=0,
        )
