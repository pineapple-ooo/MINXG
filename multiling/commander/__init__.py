"""
AgentHarness Commander Framework — Hierarchical Multi-Agent Orchestration
====================================================================
A top-level Commander AI coordinates 3–15 sub-agents in a task board
model. The commander plans the project, dispatches tasks, reassigns
idle agents to the hardest open work, can step in to do work itself,
handles agent failures, prevents file-edit collisions via resource
claims, and keeps a sideline reviewer/auditor. Supports up to 2
commanders via CommanderCouncil.

Design philosophy (from the project spec):
  - The Commander is *also* a worker — it can join any task and do
    real work, not just manage. Complex projects need every hand.
  - Idle agents are never wasted — the Commander pulls them toward
    the hardest open task and notifies the agents already on it.
  - Overstaffed tasks get thinned — the Commander can pull agents
    off a task that has too many workers and redirect them.
  - Failure is graceful — when an agent fails, the Commander picks
    one of: replace it directly, let a coworker absorb the work,
    or pair-up two agents on the orphaned task.
  - Conflicts are prevented, not detected-after-the-fact — agents
    claim resources (file paths, branch names) before touching them.
  - Anti-loop is prompt-based, not a hard breaker — the spec
    explicitly says breakers make the AI stall forever. We inject
    trajectory hints and escalate via prompts instead.
  - A sideline reviewer is always present, critiquing and auditing.
  - Up to 2 commanders can coordinate via CommanderCouncil.

Core concepts:
  - TaskBoard     — shared board of tasks with difficulty/claims/dependencies
  - AgentPool     — managed pool of 3–15 agents with lifecycle + idle detection
  - ConflictGuard — resource claim system (file paths, git branches, ...)
  - LoopGuardian  — prompt-based anti-loop (no hard breaker)
  - Reviewer      — sideline critic + task auditor
  - CommanderAI   — the top orchestrator that ties it all together
  - CommanderCouncil — manages up to 2 CommanderAI instances
  - CommanderSession — user-facing session wrapper
"""

from .task_board import Task, TaskBoard, TaskStatus, TaskDifficulty
from .conflict_guard import ConflictGuard, ResourceClaim, ClaimResult
from .anti_loop import LoopGuardian, LoopSignal
from .agent_pool import ManagedAgent, AgentPool, AgentState
from .reviewer import Reviewer, ReviewResult
from .commander import CommanderAI
from .council import CommanderCouncil
from .session import CommanderSession

__all__ = [
    # task board
    "Task", "TaskBoard", "TaskStatus", "TaskDifficulty",
    # conflict guard
    "ConflictGuard", "ResourceClaim", "ClaimResult",
    # anti-loop
    "LoopGuardian", "LoopSignal",
    # agent pool
    "ManagedAgent", "AgentPool", "AgentState",
    # reviewer
    "Reviewer", "ReviewResult",
    # commander
    "CommanderAI", "CommanderCouncil", "CommanderSession",
]