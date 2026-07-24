"""
agent_harness.contracts.agent.sandbox — Agent Training Sandbox & Recursive Improvement
================================================================================

Bold design: agents learn by doing in an isolated sandbox, then promote
successful strategies to production via recursive self-improvement loops.

Components
----------
1. **SandboxedWorkspace** — isolated filesystem + execution environment
2. **TrainingScenario** — reproducible challenge with success criteria
3. **TrainingRun** — execution trace, rewards, penalties, episode buffer
4. **RecursiveImprovementLoop** — read own history -> redesign self -> verify -> deploy
5. **StrategyPromotion** — gate for promoting sandbox strategies to live runtime
"""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .autonomous_engine import (
    CodebaseIntelligence,
    DesignPlan,
    ImplementationPatch,
    Opportunity,
    Verifier,
    VerificationResult,
)
from .evolution import (
    AgentStrategyStore,
    EvolutionaryEngine,
    FitnessMetrics,
    StrategyGenome,
)

logger = logging.getLogger(__name__)


__all__ = [
"SandboxedWorkspace",
"TrainingScenario",
"TrainingRun",
"RecursiveImprovementLoop",
"StrategyPromotion",
"TrainingReport",
]

# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

@dataclass
class SandboxedWorkspace:
    """Ephemeral isolated workspace for agent experimentation."""
    root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "agent_harness_sandbox")
    run_id: str = field(default_factory=lambda: hashlib.sha256(str(time.time()).encode()).hexdigest()[:12])
    _path: Optional[Path] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._path = self.root / self.run_id
        self._path.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path or self.root / self.run_id

    def copy_from(self, source: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, self.path / source.name, dirs_exist_ok=True)
        else:
            shutil.copy2(source, self.path / source.name)

    def run_command(self, cmd: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=self.path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def cleanup(self) -> None:
        if self._path and self._path.exists():
            shutil.rmtree(self._path, ignore_errors=True)

# ---------------------------------------------------------------------------
# Training Scenario
# ---------------------------------------------------------------------------

@dataclass
class TrainingScenario:
    """A reproducible challenge for agent training."""
    scenario_id: str
    name: str
    description: str
    setup_commands: List[str]
    success_criteria: Dict[str, Any]
    difficulty: str = "medium"
    tags: List[str] = field(default_factory=list)
    timeout_s: int = 120

    def setup(self, workspace: SandboxedWorkspace) -> None:
        for cmd in self.setup_commands:
            result = workspace.run_command(["bash", "-lc", cmd])
            if result.returncode != 0:
                logger.warning("setup command failed: %s", cmd)

@dataclass
class TrainingRun:
    """Single execution of a scenario."""
    run_id: str
    scenario: TrainingScenario
    workspace: SandboxedWorkspace
    trace: List[Dict[str, Any]] = field(default_factory=list)
    rewards: List[float] = field(default_factory=list)
    penalties: List[float] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    passed: bool = False
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None

    def log(self, event: str, data: Dict[str, Any]) -> None:
        self.trace.append({"ts": time.time(), "event": event, **data})

    def reward(self, amount: float, reason: str) -> None:
        self.rewards.append(amount)
        self.log("reward", {"amount": amount, "reason": reason})

    def penalty(self, amount: float, reason: str) -> None:
        self.penalties.append(amount)
        self.log("penalty", {"amount": amount, "reason": reason})

    def score(self) -> float:
        return sum(self.rewards) - sum(self.penalties)

    def finalize(self, result: Dict[str, Any], passed: bool) -> None:
        self.result = result
        self.passed = passed
        self.finished_at = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Recursive Improvement Loop
# ---------------------------------------------------------------------------

class RecursiveImprovementLoop:
    """Bold core: agents redesign themselves from execution history."""

    def __init__(
        self,
        project_root: Path,
        store: Optional[AgentStrategyStore] = None,
        verifier: Optional[Verifier] = None,
        max_iterations: int = 5,
    ) -> None:
        self.project_root = project_root
        self.engine = AutonomousEngine(project_root, verifier=verifier)
        self.store = store or AgentStrategyStore()
        self.evolution = EvolutionaryEngine(store=self.store)
        self.max_iterations = max_iterations
        self.history: List[Dict[str, Any]] = []

    async def run(self, goal: str) -> Dict[str, Any]:
        best_score = 0.0
        best_patches: List[ImplementationPatch] = []
        for i in range(self.max_iterations):
            logger.info("recursive improvement iteration %d/%d", i + 1, self.max_iterations)
            cycle_result = await self.engine.run_autonomous_cycle(goal)
            score = cycle_result.get("patches_approved", 0) - cycle_result.get("patches_rolled_back", 0)
            if score > best_score:
                best_score = score
                best_patches = getattr(self.engine, "_last_patches", [])
            metrics = FitnessMetrics(
                success_rate=cycle_result.get("patches_approved", 0) / max(cycle_result.get("patches_attempted", 1), 1),
                avg_latency_s=1.0,
                token_cost=1000,
                user_rating=min(1.0, score / 10.0),
                retry_rate=cycle_result.get("patches_rolled_back", 0) / max(cycle_result.get("patches_attempted", 1), 1),
            )
            self.evolution.evaluate(f"loop_{i}", metrics)
            if i < self.max_iterations - 1:
                self.evolution.evolve_generation(mutation_rate=0.2)
        best = self.evolution.best_strategy()
        return {
            "goal": goal,
            "iterations": self.max_iterations,
            "best_score": best_score,
            "best_strategy": best.fingerprint() if best else None,
            "best_fitness": best.fitness.composite_score() if best else 0.0,
        }

# ---------------------------------------------------------------------------
# Strategy Promotion
# ---------------------------------------------------------------------------

class StrategyPromotion:
    """Gated promotion from sandbox to production."""

    def __init__(self, gate_threshold: float = 0.85) -> None:
        self.gate_threshold = gate_threshold

    def evaluate(self, genome: StrategyGenome, training_runs: List[TrainingRun]) -> Dict[str, Any]:
        if not training_runs:
            return {"approved": False, "reason": "no_training_runs"}
        pass_rate = sum(1 for r in training_runs if r.passed) / len(training_runs)
        avg_score = sum(r.score() for r in training_runs) / len(training_runs)
        approved = pass_rate >= self.gate_threshold and avg_score > 0
        return {
            "approved": approved,
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "gate_threshold": self.gate_threshold,
            "reason": "meets_criteria" if approved else "below_threshold",
        }

# ---------------------------------------------------------------------------
# Training Report
# ---------------------------------------------------------------------------

@dataclass
class TrainingReport:
    goal: str
    iterations: int
    best_score: float
    best_strategy_fingerprint: Optional[str]
    best_fitness: float
    patches_attempted: int
    patches_approved: int
    training_runs: List[TrainingRun]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "iterations": self.iterations,
            "best_score": self.best_score,
            "best_strategy": self.best_strategy_fingerprint,
            "best_fitness": self.best_fitness,
            "patches_attempted": self.patches_attempted,
            "patches_approved": self.patches_approved,
            "training_runs": len(self.training_runs),
            "timestamp": self.created_at,
        }
