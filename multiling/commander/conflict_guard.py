"""
conflict_guard.py — Resource claim system prevents file-edit collisions.

The spec's hardest coordination problem: "Agent1 changes file A,
another agent that came to help also changes file A — causing chaos."

Solution: before an agent touches a resource (file path, git branch,
API endpoint, port, etc.) it must CLAIM it through the ConflictGuard.
If the resource is already claimed by another active task, the claim
is refused and the agent either waits, asks the Commander to
arbitrate, or works on a non-conflicting resource instead.

Claims are tied to (task_id, agent_id) pairs. When a task completes
or an agent is reassigned, its claims are released automatically.

This is preventive, not detective — we block the collision *before*
it happens, not after two agents have clobbered each other's writes.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


class ClaimResult(enum.Enum):
    """Outcome of a resource claim attempt."""
    GRANTED = "granted"          # claim succeeded
    OWNED = "owned"             # already owned by this (task, agent)
    CONFLICT = "conflict"       # owned by a different (task, agent)
    RELEASED = "released"       # claim was released

    def __str__(self) -> str:
        return self.value


@dataclass
class ResourceClaim:
    """A single active claim on a resource.

    Attributes:
        resource: the resource identifier (file path, branch name, ...).
        task_id: the task that owns this claim.
        agent_id: the agent within the task that made the claim.
        claim_type: "exclusive" (no one else may touch) or "shared"
            (multiple readers OK, used for read-only access).
        claimed_at: timestamp.
    """
    resource: str
    task_id: str
    agent_id: str
    claim_type: str = "exclusive"  # "exclusive" | "shared"
    claimed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "claim_type": self.claim_type,
            "claimed_at": self.claimed_at,
        }


class ConflictGuard:
    """Thread-safe resource claim registry.

    Prevents two agents from simultaneously editing the same file,
    pushing to the same git branch, or otherwise colliding on a
    shared resource.

    Usage::

        guard = ConflictGuard()

        # Agent tries to claim a file for its task
        result = guard.claim("src/main.py", task_id="t1", agent_id="a1")
        if result == ClaimResult.GRANTED:
            # safe to edit
            ...
        elif result == ClaimResult.CONFLICT:
            # another task owns this file — wait or ask Commander
            owner = guard.get_owner("src/main.py")
            ...

        # when done
        guard.release_for_task("t1")
    """

    def __init__(self):
        # resource -> active claim
        self._claims: Dict[str, ResourceClaim] = {}
        # task_id -> set of resources (for fast batch release)
        self._task_index: Dict[str, Set[str]] = {}
        self._lock = threading.RLock()

    def claim(self, resource: str, task_id: str, agent_id: str,
               claim_type: str = "exclusive") -> ClaimResult:
        """Attempt to claim a resource for (task_id, agent_id).

        Returns:
            GRANTED  — claim was fresh, now owned by this pair.
            OWNED    — already owned by this exact pair (idempotent).
            CONFLICT — owned by a different pair; caller must not
                       touch the resource.
        """
        with self._lock:
            existing = self._claims.get(resource)
            if existing is not None:
                # Same task+agent — idempotent re-claim is fine.
                if (existing.task_id == task_id
                        and existing.agent_id == agent_id):
                    return ClaimResult.OWNED
                # Shared read-only claims can coexist.
                if (claim_type == "shared"
                        and existing.claim_type == "shared"):
                    return ClaimResult.GRANTED
                # Exclusive claim by a different pair — conflict.
                return ClaimResult.CONFLICT

            # No existing claim — grant it.
            claim = ResourceClaim(
                resource=resource,
                task_id=task_id,
                agent_id=agent_id,
                claim_type=claim_type,
            )
            self._claims[resource] = claim
            self._task_index.setdefault(task_id, set()).add(resource)
            return ClaimResult.GRANTED

    def claim_batch(self, resources: List[str], task_id: str,
                    agent_id: str,
                    claim_type: str = "exclusive") -> Tuple[ClaimResult, List[str]]:
        """Try to claim multiple resources atomically.

        If ANY resource conflicts, NOTHING is claimed (all-or-nothing).
        Returns (result, conflicting_resources). On GRANTED, the
        conflicting list is empty.
        """
        with self._lock:
            # First pass: check all for conflicts without claiming.
            conflicting: List[str] = []
            for r in resources:
                existing = self._claims.get(r)
                if existing is not None:
                    if (existing.task_id == task_id
                            and existing.agent_id == agent_id):
                        continue  # already ours
                    if (claim_type == "shared"
                            and existing.claim_type == "shared"):
                        continue  # shared OK
                    conflicting.append(r)
            if conflicting:
                return ClaimResult.CONFLICT, conflicting

            # Second pass: claim all.
            for r in resources:
                if r not in self._claims:
                    self._claims[r] = ResourceClaim(
                        resource=r, task_id=task_id, agent_id=agent_id,
                        claim_type=claim_type,
                    )
                    self._task_index.setdefault(task_id, set()).add(r)
            return ClaimResult.GRANTED, []

    def release(self, resource: str, task_id: str,
                agent_id: str) -> bool:
        """Release a specific claim. Returns True if released."""
        with self._lock:
            claim = self._claims.get(resource)
            if claim is None:
                return False
            if claim.task_id != task_id or claim.agent_id != agent_id:
                return False
            del self._claims[resource]
            task_set = self._task_index.get(task_id)
            if task_set is not None:
                task_set.discard(resource)
                if not task_set:
                    del self._task_index[task_id]
            return True

    def release_for_task(self, task_id: str) -> int:
        """Release ALL claims owned by a task. Used when a task
        completes, fails, or is cancelled. Returns count released."""
        with self._lock:
            resources = self._task_index.pop(task_id, set())
            for r in resources:
                claim = self._claims.get(r)
                # Only delete if the claim belongs to this task
                # (shared claims might belong to multiple tasks).
                if claim is not None and claim.task_id == task_id:
                    del self._claims[r]
            return len(resources)

    def release_for_agent(self, task_id: str,
                          agent_id: str) -> int:
        """Release claims owned by a specific agent within a task.
        Used when the Commander reassigns an agent away from a task."""
        with self._lock:
            released = 0
            task_resources = self._task_index.get(task_id, set()).copy()
            for r in task_resources:
                claim = self._claims.get(r)
                if claim is not None and claim.agent_id == agent_id:
                    del self._claims[r]
                    self._task_index.get(task_id, set()).discard(r)
                    released += 1
            # Clean up empty task index entries.
            if task_id in self._task_index and not self._task_index[task_id]:
                del self._task_index[task_id]
            return released

    def get_owner(self, resource: str) -> Optional[ResourceClaim]:
        """Who currently owns a resource?"""
        with self._lock:
            return self._claims.get(resource)

    def check_conflict(self, resources: List[str],
                        task_id: str, agent_id: str) -> List[str]:
        """Return the subset of resources that are currently
        claimed by a DIFFERENT (task, agent) pair. Empty list = no
        conflicts."""
        with self._lock:
            conflicting: List[str] = []
            for r in resources:
                existing = self._claims.get(r)
                if existing is None:
                    continue
                if (existing.task_id == task_id
                        and existing.agent_id == agent_id):
                    continue
                conflicting.append(r)
            return conflicting

    def list_claims(self, task_id: Optional[str] = None) -> List[ResourceClaim]:
        """List all active claims, optionally filtered by task."""
        with self._lock:
            claims = list(self._claims.values())
            if task_id is not None:
                claims = [c for c in claims if c.task_id == task_id]
            return claims

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_claims": len(self._claims),
                "tasks_with_claims": len(self._task_index),
                "resources_claimed": len(self._claims),
            }

    def clear(self) -> None:
        """Release everything. Used at session reset."""
        with self._lock:
            self._claims.clear()
            self._task_index.clear()
