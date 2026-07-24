"""
minxg.contracts.agent.evolution — Evolutionary Self-Improvement
===============================================================

Bold design: agents modify their own strategies, prompts, and tool configs
based on performance history.

Mechanisms
----------
1. **Strategy Genome** — each agent has a configurable strategy vector
2. **Fitness Function** — success rate, latency, token cost, user feedback
3. **Mutation** — prompt rewrites, tool reordering, parameter tuning
4. **Crossover** — combine successful strategies from different agents
5. **Selection** — elitism + tournament selection for survival
6. **Epigenetics** — fast adaptation via temporary strategy overlays
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


__all__ = [
"StrategyGenome",
"FitnessMetrics",
"MutationOperator",
"CrossoverOperator",
"EvolutionaryEngine",
"AgentStrategyStore",
]

# ---------------------------------------------------------------------------
# Strategy Genome
# ---------------------------------------------------------------------------

@dataclass
class FitnessMetrics:
    success_rate: float = 0.0
    avg_latency_s: float = 0.0
    token_cost: int = 0
    user_rating: float = 0.0
    retry_rate: float = 0.0

    def composite_score(self, weights: Optional[Dict[str, float]] = None) -> float:
        w = weights or {"success_rate": 0.4, "avg_latency_s": -0.2, "token_cost": -0.1, "user_rating": 0.3}
        score = 0.0
        score += w.get("success_rate", 0.0) * self.success_rate
        score += w.get("avg_latency_s", 0.0) * max(0.0, 1.0 - min(self.avg_latency_s / 10.0, 1.0))
        score += w.get("token_cost", 0.0) * max(0.0, 1.0 - min(self.token_cost / 10000, 1.0))
        score += w.get("user_rating", 0.0) * self.user_rating
        return max(0.0, min(1.0, score))

@dataclass
class StrategyGenome:
    """Encoded strategy for an agent."""
    agent_id: str
    prompt_template: str
    tool_order: List[str]
    retry_policy: Dict[str, Any]
    temperature: float = 0.7
    max_tokens: int = 4096
    system_prompt_additions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    fitness: FitnessMetrics = field(default_factory=FitnessMetrics)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def mutate(self, mutation_rate: float = 0.1) -> "StrategyGenome":
        child = copy.deepcopy(self)
        child.generation += 1
        child.parent_ids = [self.agent_id]
        if random.random() < mutation_rate:
            child.temperature = max(0.0, min(2.0, self.temperature + random.uniform(-0.2, 0.2)))
        if random.random() < mutation_rate and self.tool_order:
            idx = random.randint(0, len(self.tool_order) - 1)
            swap_idx = random.randint(0, len(self.tool_order) - 1)
            child.tool_order[idx], child.tool_order[swap_idx] = child.tool_order[swap_idx], child.tool_order[idx]
        if random.random() < mutation_rate and self.system_prompt_additions:
            child.system_prompt_additions.append(f"reflection_gen_{child.generation}")
        return child

    def crossover(self, other: "StrategyGenome") -> "StrategyGenome":
        child = copy.deepcopy(self)
        child.generation = max(self.generation, other.generation) + 1
        child.parent_ids = [self.agent_id, other.agent_id]
        split = len(self.tool_order) // 2
        child.tool_order = self.tool_order[:split] + other.tool_order[split:]
        child.temperature = (self.temperature + other.temperature) / 2
        return child

    def fingerprint(self) -> str:
        payload = json.dumps({
            "t": self.temperature,
            "m": self.max_tokens,
            "o": self.tool_order,
            "p": self.prompt_template[:64],
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Mutation & Crossover Operators
# ---------------------------------------------------------------------------

class MutationOperator:
    @staticmethod
    def prompt_rewrite(genome: StrategyGenome, style: str = "concise") -> StrategyGenome:
        child = copy.deepcopy(genome)
        prefixes = {"concise": "Be brief. ", "detailed": "Be thorough. ", "socratic": "Question assumptions. "}
        child.prompt_template = prefixes.get(style, "") + child.prompt_template
        return child

    @staticmethod
    def temperature_nudge(genome: StrategyGenome, delta: float = 0.1) -> StrategyGenome:
        child = copy.deepcopy(genome)
        child.temperature = max(0.0, min(2.0, genome.temperature + delta))
        return child

    @staticmethod
    def tool_reorder(genome: StrategyGenome) -> StrategyGenome:
        child = copy.deepcopy(genome)
        if len(child.tool_order) > 1:
            i, j = random.sample(range(len(child.tool_order)), 2)
            child.tool_order[i], child.tool_order[j] = child.tool_order[j], child.tool_order[i]
        return child

class CrossoverOperator:
    @staticmethod
    def single_point(g1: StrategyGenome, g2: StrategyGenome) -> StrategyGenome:
        return g1.crossover(g2)

# ---------------------------------------------------------------------------
# Agent Strategy Store
# ---------------------------------------------------------------------------

class AgentStrategyStore:
    """Persistent storage for strategy genomes and fitness history."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self.storage_path = storage_path or Path(".minxg/agent_strategies.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._store: Dict[str, StrategyGenome] = {}
        self._load()

    def _load(self) -> None:
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for agent_id, genome in data.items():
                    self._store[agent_id] = StrategyGenome(**genome)
            except Exception:
                pass

    def save(self) -> None:
        payload = {aid: {
            "agent_id": g.agent_id,
            "prompt_template": g.prompt_template,
            "tool_order": g.tool_order,
            "retry_policy": g.retry_policy,
            "temperature": g.temperature,
            "max_tokens": g.max_tokens,
            "system_prompt_additions": g.system_prompt_additions,
            "generation": g.generation,
            "parent_ids": g.parent_ids,
            "fitness": {
                "success_rate": g.fitness.success_rate,
                "avg_latency_s": g.fitness.avg_latency_s,
                "token_cost": g.fitness.token_cost,
                "user_rating": g.fitness.user_rating,
                "retry_rate": g.fitness.retry_rate,
            },
        } for aid, g in self._store.items()}
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, agent_id: str) -> Optional[StrategyGenome]:
        return self._store.get(agent_id)

    def put(self, genome: StrategyGenome) -> None:
        self._store[genome.agent_id] = genome
        self.save()

# ---------------------------------------------------------------------------
# Evolutionary Engine
# ---------------------------------------------------------------------------

class EvolutionaryEngine:
    """Evolve agent strategies via selection, mutation, crossover."""

    def __init__(self, store: Optional[AgentStrategyStore] = None) -> None:
        self.store = store or AgentStrategyStore()
        self.population: Dict[str, StrategyGenome] = {}
        self.history: List[Dict[str, Any]] = []

    def register(self, genome: StrategyGenome) -> None:
        self.population[genome.agent_id] = genome
        self.store.put(genome)

    def evaluate(self, agent_id: str, metrics: FitnessMetrics) -> float:
        if agent_id in self.population:
            self.population[agent_id].fitness = metrics
            self.store.put(self.population[agent_id])
        return metrics.composite_score()

    def select(self, n: int = 2) -> List[StrategyGenome]:
        scored = sorted(self.population.values(), key=lambda g: g.fitness.composite_score(), reverse=True)
        return scored[:n]

    def evolve_generation(self, mutation_rate: float = 0.15) -> List[StrategyGenome]:
        elites = self.select(max(2, len(self.population) // 4))
        new_gen = [copy.deepcopy(e) for e in elites]
        while len(new_gen) < len(self.population):
            if len(elites) >= 2 and random.random() < 0.7:
                p1, p2 = random.sample(elites, 2)
                child = CrossoverOperator.single_point(p1, p2)
            else:
                parent = random.choice(elites)
                child = parent.mutate(mutation_rate)
            new_gen.append(child)
        for genome in new_gen:
            self.store.put(genome)
        self.population = {g.agent_id: g for g in new_gen}
        self.history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "population_size": len(new_gen),
            "elite_count": len(elites),
        })
        return new_gen

    def best_strategy(self) -> Optional[StrategyGenome]:
        if not self.population:
            return None
        return max(self.population.values(), key=lambda g: g.fitness.composite_score())
