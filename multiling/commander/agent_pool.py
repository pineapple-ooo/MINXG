"""
agent_pool.py — Managed agent pool with lifecycle, idle detection, and
failure handling for the Commander framework.

ManagedAgent wraps the existing multiling.agent.Agent with:
  - lifecycle state (idle/working/done/failed/offline)
  - current task assignment tracking
  - a per-agent LoopGuardian (anti-loop)
  - idle-since timestamp (for Commander redirection logic)
  - failure flag + reason

AgentPool manages 3–15 ManagedAgents:
  - spawn / retire
  - find idle agents (the Commander redirects these)
  - find agents on a given task (for "join in" notifications)
  - handle failure (mark agent failed, let Commander decide
    replacement / absorption / pair-up)

The pool itself does NOT call AI models — that's the Commander's job
via the injectable handler, same pattern as
tools/delegate_tool.py::SubagentPool. The pool manages *metadata and
state*; execution is delegated out.
"""

from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .anti_loop import LoopGuardian


MIN_AGENTS = 3
MAX_AGENTS = 15
DEFAULT_AGENTS = 5


class AgentState(enum.Enum):
    """Lifecycle state of a ManagedAgent."""
    IDLE = "idle"            # not currently assigned to any task
    WORKING = "working"      # actively executing a task
    REPORTING = "reporting"  # task done, reporting to Commander
    FAILED = "failed"        # crashed or timed out
    OFFLINE = "offline"      # retired / shut down

    def __str__(self) -> str:
        return self.value


@dataclass
class ManagedAgent:
    """An agent under the Commander's management.

    Wraps multiling.agent.Agent (or a compatible agent object) with
    lifecycle and coordination metadata.

    Attributes:
        id: unique agent id (agent_xxxx).
        name: human-friendly name for display.
        role: the agent's role (coder, reviewer, planner, ...).
        agent: the underlying Agent object (from multiling.agent).
            May be None in test/mock mode.
        state: current lifecycle state.
        current_task_id: the task this agent is working on (or None).
        idle_since: timestamp when the agent last became idle.
        working_since: timestamp when the agent started its current task.
        loop_guardian: per-agent anti-loop guardian.
        failure_reason: if state == FAILED, why.
        total_tasks_completed: lifetime count.
        total_errors: lifetime error count.
        created_at: pool-join timestamp.
    """
    id: str = field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:10]}")
    name: str = ""
    role: str = "coder"
    agent: Any = None  # multiling.agent.Agent or compatible
    state: AgentState = AgentState.IDLE
    current_task_id: Optional[str] = None
    idle_since: Optional[float] = field(default_factory=time.time)
    working_since: Optional[float] = None
    loop_guardian: LoopGuardian = field(default_factory=LoopGuardian)
    failure_reason: Optional[str] = None
    total_tasks_completed: int = 0
    total_errors: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- state transitions --------------------------------------------

    def assign_to(self, task_id: str) -> None:
        """Transition IDLE -> WORKING on a task."""
        self.current_task_id = task_id
        self.state = AgentState.WORKING
        self.working_since = time.time()
        self.idle_since = None

    def mark_idle(self) -> None:
        """Transition to IDLE. Called when a task completes or the
        Commander reassigns the agent."""
        self.current_task_id = None
        self.state = AgentState.IDLE
        self.idle_since = time.time()
        self.working_since = None

    def mark_reporting(self) -> None:
        """Transition to REPORTING (task done, waiting for Commander
        to accept the result)."""
        self.state = AgentState.REPORTING

    def mark_failed(self, reason: str) -> None:
        """Transition to FAILED. Keeps current_task_id so the
        Commander can determine which task was orphaned."""
        self.state = AgentState.FAILED
        self.failure_reason = reason
        # Do NOT clear current_task_id — the Commander needs it
        # to find the orphaned task in _handle_failures.

    def mark_offline(self) -> None:
        """Retire the agent."""
        self.state = AgentState.OFFLINE
        self.current_task_id = None

    def reset(self) -> None:
        """Reset to IDLE, clear failure. Used when the Commander
        decides to retry a failed agent."""
        self.state = AgentState.IDLE
        self.current_task_id = None
        self.failure_reason = None
        self.idle_since = time.time()
        self.working_since = None
        self.loop_guardian.reset()

    def is_idle(self) -> bool:
        return self.state == AgentState.IDLE

    def is_working(self) -> bool:
        return self.state == AgentState.WORKING

    def is_failed(self) -> bool:
        return self.state == AgentState.FAILED

    def is_available(self) -> bool:
        """True if the agent can accept a new task."""
        return self.state in (AgentState.IDLE,)

    def idle_duration(self) -> float:
        """Seconds since this agent became idle (0 if not idle)."""
        if self.idle_since is None:
            return 0.0
        return time.time() - self.idle_since

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "state": str(self.state),
            "current_task_id": self.current_task_id,
            "idle_since": self.idle_since,
            "working_since": self.working_since,
            "failure_reason": self.failure_reason,
            "total_tasks_completed": self.total_tasks_completed,
            "total_errors": self.total_errors,
            "uptime_sec": round(time.time() - self.created_at, 2),
        }


class AgentPool:
    """Thread-safe pool of 3–15 ManagedAgents.

    The Commander uses this pool to:
      - spawn agents up to the max
      - find idle agents for redirection
      - find agents working on a specific task (for join notifications)
      - handle failures (mark failed, let Commander decide replacement)
    """

    def __init__(self, min_agents: int = MIN_AGENTS,
                 max_agents: int = MAX_AGENTS):
        self.min_agents = min_agents
        self.max_agents = max_agents
        self._agents: Dict[str, ManagedAgent] = {}
        self._lock = threading.RLock()

    # --- spawn / retire -----------------------------------------------

    def spawn(self, name: str = "", role: str = "coder",
              agent: Any = None) -> Optional[ManagedAgent]:
        """Add a new agent to the pool. Returns None if the pool is full."""
        with self._lock:
            if len(self._agents) >= self.max_agents:
                return None
            ma = ManagedAgent(
                name=name or f"agent-{len(self._agents) + 1}",
                role=role,
                agent=agent,
            )
            self._agents[ma.id] = ma
            return ma

    def ensure_min_agents(self, agent_factory: Optional[Callable] = None) -> int:
        """Ensure the pool has at least min_agents agents. Creates
        placeholder agents (agent=None) if no factory is given.
        Returns the number of agents created."""
        with self._lock:
            created = 0
            while len(self._agents) < self.min_agents:
                ag = None
                if agent_factory is not None:
                    ag = agent_factory()
                ma = ManagedAgent(
                    name=f"agent-{len(self._agents) + 1}",
                    role="coder",
                    agent=ag,
                )
                self._agents[ma.id] = ma
                created += 1
            return created

    def retire(self, agent_id: str) -> bool:
        """Mark an agent as OFFLINE (does not remove from pool — keeps
        history). Returns True if the agent was found."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None:
                return False
            ma.mark_offline()
            return True

    def remove(self, agent_id: str) -> bool:
        """Actually remove an agent from the pool."""
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                return True
            return False

    # --- lookup --------------------------------------------------------

    def get(self, agent_id: str) -> Optional[ManagedAgent]:
        with self._lock:
            return self._agents.get(agent_id)

    def list_all(self) -> List[ManagedAgent]:
        with self._lock:
            return list(self._agents.values())

    def list_active(self) -> List[ManagedAgent]:
        """All agents that are not OFFLINE."""
        with self._lock:
            return [a for a in self._agents.values()
                    if a.state != AgentState.OFFLINE]

    def list_idle(self) -> List[ManagedAgent]:
        """All IDLE agents — candidates for redirection."""
        with self._lock:
            return [a for a in self._agents.values()
                    if a.state == AgentState.IDLE]

    def get_idle_count(self) -> int:
        return len(self.list_idle())

    def get_working_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._agents.values()
                       if a.state == AgentState.WORKING)

    def list_working_on(self, task_id: str) -> List[ManagedAgent]:
        """All agents currently working on a given task — used by the
        Commander to notify existing workers when a new agent joins."""
        with self._lock:
            return [a for a in self._agents.values()
                    if a.state == AgentState.WORKING
                    and a.current_task_id == task_id]

    def list_failed(self) -> List[ManagedAgent]:
        with self._lock:
            return [a for a in self._agents.values()
                    if a.state == AgentState.FAILED]

    def find_longest_idle(self) -> Optional[ManagedAgent]:
        """The idle agent that's been idle the longest — first
        candidate for redirection to the hardest task."""
        idle = self.list_idle()
        if not idle:
            return None
        now = time.time()
        return min(idle, key=lambda a: a.idle_since if a.idle_since is not None else now)

    # --- assignment helpers --------------------------------------------

    def assign(self, agent_id: str, task_id: str) -> bool:
        """Assign an idle agent to a task."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None or not ma.is_available():
                return False
            ma.assign_to(task_id)
            return True

    def release(self, agent_id: str) -> bool:
        """Mark an agent idle (task done or reassigned)."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None:
                return False
            ma.mark_idle()
            return True

    def mark_completed(self, agent_id: str, task_id: str) -> bool:
        """Record that an agent finished its task. Increments the
        completed counter and marks the agent idle."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None:
                return False
            ma.total_tasks_completed += 1
            ma.mark_idle()
            return True

    def mark_failed(self, agent_id: str, reason: str) -> bool:
        """Mark an agent as failed. The Commander will later decide
        whether to replace, absorb, or pair-up."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None:
                return False
            ma.mark_failed(reason)
            ma.total_errors += 1
            return True

    def reset_agent(self, agent_id: str) -> bool:
        """Reset a failed agent back to idle (Commander decided to
        retry rather than retire)."""
        with self._lock:
            ma = self._agents.get(agent_id)
            if ma is None:
                return False
            ma.reset()
            return True

    # --- summary --------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for a in self._agents.values():
                key = str(a.state)
                counts[key] = counts.get(key, 0) + 1
            return {
                "total_agents": len(self._agents),
                "by_state": counts,
                "idle_count": sum(1 for a in self._agents.values()
                                  if a.state == AgentState.IDLE),
                "working_count": sum(1 for a in self._agents.values()
                                     if a.state == AgentState.WORKING),
                "failed_count": sum(1 for a in self._agents.values()
                                    if a.state == AgentState.FAILED),
                "total_completed": sum(a.total_tasks_completed
                                       for a in self._agents.values()),
                "total_errors": sum(a.total_errors
                                    for a in self._agents.values()),
            }

    def size(self) -> int:
        with self._lock:
            return len(self._agents)
