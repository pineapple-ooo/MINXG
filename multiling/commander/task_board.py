"""
task_board.py — Shared task board for multi-agent coordination.

The TaskBoard is the single source of truth for what work exists,
who's doing what, and what's done. The Commander and every agent can
read it; only the Commander (and agents reporting completion) mutate
it, through well-defined methods.

Key design choices driven by the spec:
  - Tasks carry a difficulty score (1–10) so the Commander can
    redirect idle agents to the *hardest* open work, not just any
    open work.
  - Tasks carry a list of claimed resources (file paths, branch
    names) so the ConflictGuard can prevent two agents from editing
    the same file simultaneously.
  - Tasks carry dependencies — a task isn't assignable until all its
    dependencies are completed.
  - Tasks carry a list of assigned agent IDs and a max_assignees
    cap so the Commander can detect overstaffing and thin a task.
"""

from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class TaskStatus(enum.Enum):
    """Lifecycle states a task can be in."""
    PENDING = "pending"          # not yet assigned
    ASSIGNED = "assigned"        # at least one agent working
    IN_REVIEW = "in_review"      # submitted for review, not yet approved
    COMPLETED = "completed"      # reviewed and approved
    FAILED = "failed"            # an agent failed and work is stalled
    BLOCKED = "blocked"          # waiting on a dependency or resource

    def __str__(self) -> str:
        return self.value


class TaskDifficulty(enum.IntEnum):
    """Coarse difficulty buckets. Higher = harder."""
    TRIVIAL = 1
    EASY = 3
    MEDIUM = 5
    HARD = 7
    CRITICAL = 10


@dataclass
class Task:
    """A unit of work on the task board.

    Attributes:
        id: stable unique id.
        title: short human-readable summary.
        description: full task description (what to do, constraints).
        difficulty: 1–10 integer; drives idle-agent redirection.
        dependencies: list of task IDs that must be COMPLETED before
            this task becomes assignable.
        resources: list of resource identifiers (file paths, branch
            names, API endpoints) this task will touch — the
            ConflictGuard checks these before assignment.
        assigned_agents: live list of agent IDs currently working
            this task. Multiple agents can collaborate on one task.
        max_assignees: ceiling on assigned_agents. When exceeded the
            Commander thins the roster.
        status: current lifecycle state.
        created_at / started_at / completed_at: timestamps.
        result: the completed work product (text — summary, diff, etc.).
        review_feedback: feedback from the Reviewer, if any.
        failure_reason: if status == FAILED, why.
        metadata: free-form bag for the Commander / agents.
    """
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:10]}")
    title: str = ""
    description: str = ""
    difficulty: int = TaskDifficulty.MEDIUM
    dependencies: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    assigned_agents: List[str] = field(default_factory=list)
    max_assignees: int = 3
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    review_feedback: Optional[str] = None
    failure_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- helpers ------------------------------------------------------

    def is_open(self) -> bool:
        """True if the task still needs work (not completed/failed)."""
        return self.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED,
                                TaskStatus.BLOCKED)

    def is_assignable(self, completed_dep_ids: set) -> bool:
        """True if all dependencies are in ``completed_dep_ids``."""
        if not self.is_open():
            return False
        if self.status == TaskStatus.ASSIGNED and len(self.assigned_agents) >= self.max_assignees:
            return False
        return all(d in completed_dep_ids for d in self.dependencies)

    def can_accept_more_agents(self) -> bool:
        return (self.is_open()
                and len(self.assigned_agents) < self.max_assignees)

    def is_overstaffed(self) -> bool:
        """True if more agents are assigned than the cap allows."""
        return len(self.assigned_agents) > self.max_assignees

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description[:200],
            "difficulty": self.difficulty,
            "dependencies": list(self.dependencies),
            "resources": list(self.resources),
            "assigned_agents": list(self.assigned_agents),
            "max_assignees": self.max_assignees,
            "status": str(self.status),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": (self.result or "")[:200] if self.result else None,
            "failure_reason": self.failure_reason,
            "metadata": dict(self.metadata),
        }


class TaskBoard:
    """Thread-safe shared task board.

    All mutations go through methods that take the board lock; reads
    return snapshots (copies) so callers can't mutate state behind
    the board's back.
    """

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()

    # --- write API ---------------------------------------------------

    def add_task(self, task: Task) -> str:
        with self._lock:
            self._tasks[task.id] = task
            return task.id

    def create_task(self, title: str, description: str = "",
                    difficulty: int = TaskDifficulty.MEDIUM,
                    dependencies: Optional[List[str]] = None,
                    resources: Optional[List[str]] = None,
                    max_assignees: int = 3,
                    metadata: Optional[Dict[str, Any]] = None) -> Task:
        task = Task(
            title=title,
            description=description,
            difficulty=difficulty,
            dependencies=dependencies or [],
            resources=resources or [],
            max_assignees=max_assignees,
            metadata=metadata or {},
        )
        self.add_task(task)
        return task

    def assign_agent(self, task_id: str, agent_id: str) -> bool:
        """Assign an agent to a task. Returns False if the task is
        full, not open, or unknown."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            if not t.is_open():
                return False
            if agent_id in t.assigned_agents:
                return True  # already assigned — idempotent
            if len(t.assigned_agents) >= t.max_assignees:
                return False
            t.assigned_agents.append(agent_id)
            if t.status == TaskStatus.PENDING:
                t.status = TaskStatus.ASSIGNED
                t.started_at = t.started_at or time.time()
            return True

    def unassign_agent(self, task_id: str, agent_id: str) -> bool:
        """Remove an agent from a task (Commander redirecting them
        elsewhere, or agent failed). Returns True if removed."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            if agent_id in t.assigned_agents:
                t.assigned_agents.remove(agent_id)
                if not t.assigned_agents and t.status == TaskStatus.ASSIGNED:
                    t.status = TaskStatus.PENDING
                return True
            return False

    def submit_result(self, task_id: str, agent_id: str,
                      result: str) -> bool:
        """An agent reports its work product. Moves the task to
        IN_REVIEW unless the agent wasn't the last assignee."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            if agent_id not in t.assigned_agents:
                return False
            t.result = result
            t.status = TaskStatus.IN_REVIEW
            t.completed_at = time.time()
            return True

    def approve_task(self, task_id: str, feedback: str = "") -> bool:
        """Reviewer approves — task becomes COMPLETED."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            if t.status != TaskStatus.IN_REVIEW:
                return False
            t.review_feedback = feedback
            t.status = TaskStatus.COMPLETED
            t.completed_at = t.completed_at or time.time()
            return True

    def reject_task(self, task_id: str, feedback: str,
                    reassign: bool = True) -> bool:
        """Reviewer rejects — task goes back to ASSIGNED/PENDING with
        feedback attached, ready for another attempt."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            t.review_feedback = feedback
            t.result = None
            t.completed_at = None
            if reassign and t.assigned_agents:
                t.status = TaskStatus.ASSIGNED
            else:
                t.status = TaskStatus.PENDING
                t.assigned_agents.clear()
            return True

    def fail_task(self, task_id: str, agent_id: str,
                  reason: str) -> bool:
        """Mark a task as failed (an agent crashed and no one can
        absorb it)."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            t.failure_reason = f"[{agent_id}] {reason}"
            t.status = TaskStatus.FAILED
            return True

    def update_metadata(self, task_id: str, key: str, value: Any) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return False
            t.metadata[key] = value
            return True

    # --- read API ----------------------------------------------------

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            t = self._tasks.get(task_id)
            return t  # Task is a dataclass — caller gets a reference;
                       # they should not mutate it directly. For safety
                       # in read-heavy paths we trust the protocol.

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        with self._lock:
            if status is None:
                return list(self._tasks.values())
            return [t for t in self._tasks.values() if t.status == status]

    def list_all(self) -> List[Task]:
        return self.list_tasks()

    def get_completed_ids(self) -> set:
        with self._lock:
            return {tid for tid, t in self._tasks.items()
                    if t.status == TaskStatus.COMPLETED}

    def get_assignable_tasks(self) -> List[Task]:
        """Tasks whose dependencies are all satisfied and which can
        still accept agents."""
        completed = self.get_completed_ids()
        with self._lock:
            return [t for t in self._tasks.values()
                    if t.is_assignable(completed)]

    def get_hardest_open_task(self) -> Optional[Task]:
        """The single hardest open task that can accept more agents.
        Used by the Commander to redirect idle agents."""
        assignable = self.get_assignable_tasks()
        if not assignable:
            return None
        return max(assignable, key=lambda t: t.difficulty)

    def get_overstaffed_tasks(self) -> List[Task]:
        with self._lock:
            return [t for t in self._tasks.values()
                    if t.is_overstaffed()]

    def get_tasks_for_agent(self, agent_id: str) -> List[Task]:
        with self._lock:
            return [t for t in self._tasks.values()
                    if agent_id in t.assigned_agents]

    def get_pending_review(self) -> List[Task]:
        return self.list_tasks(TaskStatus.IN_REVIEW)

    def get_failed_tasks(self) -> List[Task]:
        return self.list_tasks(TaskStatus.FAILED)

    # --- summary -----------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for t in self._tasks.values():
                key = str(t.status)
                counts[key] = counts.get(key, 0) + 1
            return {
                "total_tasks": len(self._tasks),
                "by_status": counts,
                "assignable_count": len(self.get_assignable_tasks()),
                "hardest_open_difficulty": (
                    ht.difficulty if (ht := self.get_hardest_open_task()) else 0
                ),
            }
