"""
commander.py — The top-level Commander AI for hierarchical orchestration.

The Commander is the 总指挥 — it sits at the top of the multi-agent
hierarchy and is responsible for:

  1. PLANNING  — break the project goal into tasks, set difficulty
     scores, identify dependencies, pre-declare resources for conflict
     prevention.

  2. DISPATCHING — assign tasks to agents, respecting the 3–15 agent
     pool size, task dependencies, and the ConflictGuard's resource
     claims.

  3. REDIRECTING IDLE AGENTS — when an agent finishes and reports
     back, the Commander finds the hardest open task and redirects
     the idle agent there. It also notifies the agents already working
     on that task that a new helper is incoming.

  4. THINNING OVERSTAFFED TASKS — if a task has more agents than its
     cap, the Commander pulls the newest-joined agent off and
     redirects them elsewhere.

  5. DOING WORK ITSELF — the spec is explicit: the Commander can
     also be pulled into real work when a complex project needs every
     hand. ``self_work_handler`` lets the Commander execute a task
     directly (it becomes agent_0 in the pool).

  6. HANDLING FAILURES — when an agent fails, the Commander chooses:
       a) replace it directly (Commander becomes the replacement)
       b) let a coworker absorb the orphaned task (the coworker
          inherits the task and claims)
       c) pair-up: send two agents to tackle the failed task together

  7. COORDINATING WITH THE REVIEWER — submit completed tasks for
     review, act on review feedback (approve/reject/reassign).

  8. ANTI-LOOP OVERSIGHT — monitor per-agent LoopGuardians; when an
     agent's loop signal escalates, the Commander injects a steering
     prompt rather than killing the agent.

Like every other component, the Commander uses an injectable handler
(callable -> str) for AI calls. The Commander's *coordination logic*
(plan_task, redirect_idle, thin_overstaffed, handle_failure, etc.)
is fully testable without a live model.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .task_board import Task, TaskBoard, TaskStatus, TaskDifficulty
from .conflict_guard import ConflictGuard, ClaimResult
from .anti_loop import LoopGuardian, LoopSignal
from .agent_pool import ManagedAgent, AgentPool, AgentState
from .reviewer import Reviewer, ReviewResult

logger = logging.getLogger(__name__)


# ─────────────────────────── prompts ─────────────────────────────────────

PLANNER_SYSTEM_PROMPT = (
    "You are a project planner for a multi-agent coding crew. Given a "
    "project goal, break it into 2–8 concrete tasks. For each task "
    "specify: title, description, difficulty (1-10), resources (file "
    "paths / branch names it will touch), and dependencies (other task "
    "titles that must complete first). "
    "Reply with ONLY a JSON array of objects: "
    '[{"title": "...", "description": "...", "difficulty": N, '
    '"resources": ["..."], "dependencies": ["..."]}]. '
    "No prose, no markdown fences."
)

COMMANDER_SYSTEM_PROMPT = (
    "You are the Commander (总指挥) of a multi-agent coding crew. "
    "You plan the project, dispatch tasks to 3-15 sub-agents, redirect "
    "idle agents to the hardest open work, thin overstaffed tasks, "
    "handle agent failures, and can step in to do work yourself when "
    "needed. You also coordinate with a sideline Reviewer who critiques "
    "and audits. Be decisive, clear, and always surface the reasoning "
    "behind your assignments."
)


# ──────────────────── tolerant JSON parsing ───────────────────────────────

def _extract_json(text: str, opener: str, closer: str) -> Optional[Any]:
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
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse_plan(text: str) -> List[Dict[str, Any]]:
    """Parse the planner's JSON array output. Fail-safe: returns an
    empty list on parse failure rather than crashing the Commander."""
    data = _extract_json(text, "[", "]")
    if not isinstance(data, list):
        return []
    plans = []
    for item in data:
        if not isinstance(item, dict):
            continue
        plans.append({
            "title": str(item.get("title", "")),
            "description": str(item.get("description", "")),
            "difficulty": int(item.get("difficulty", 5)),
            "resources": [str(r) for r in (item.get("resources") or [])],
            "dependencies": [str(d) for d in (item.get("dependencies") or [])],
        })
    return plans


# ──────────────────── the Commander AI ──────────────────────────────────

@dataclass
class CommanderDecision:
    """A record of a decision the Commander made (for audit trail)."""
    decision_type: str  # plan, dispatch, redirect, thin, fail_handle, self_work, review
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class CommanderAI:
    """The top-level orchestrator.

    Lifecycle:
        1. ``plan(goal)`` — break the project into tasks, populate the
           TaskBoard.
        2. ``initial_dispatch()`` — assign initial tasks to idle agents.
        3. ``tick()`` — one coordination cycle: redirect idle agents,
           thin overstaffed tasks, handle failures, submit reviews.
        4. ``work_on_task(task_id)`` — the Commander steps in to do
           work itself (agent_0 pattern).
        5. ``run_to_completion()`` — loop tick() until all tasks are
           COMPLETED or FAILED.

    The Commander is NOT a thread — it doesn't run autonomously. The
    caller (CommanderSession or the CLI) drives it via ``tick()`` so
    the execution model is synchronous and testable.
    """

    def __init__(self,
                 task_board: Optional[TaskBoard] = None,
                 agent_pool: Optional[AgentPool] = None,
                 conflict_guard: Optional[ConflictGuard] = None,
                 reviewer: Optional[Reviewer] = None,
                 plan_handler: Optional[Callable] = None,
                 work_handler: Optional[Callable] = None,
                 commander_id: Optional[str] = None):
        self.board = task_board or TaskBoard()
        self.pool = agent_pool or AgentPool()
        self.conflict_guard = conflict_guard or ConflictGuard()
        self.reviewer = reviewer or Reviewer()
        self.plan_handler = plan_handler
        self.work_handler = work_handler
        self.commander_id = commander_id or f"commander_{uuid.uuid4().hex[:8]}"
        self._decisions: List[CommanderDecision] = []
        self._notifications: List[Dict[str, Any]] = []

        # The Commander is agent_0 — it can join any task and do work.
        self._commander_agent_id: Optional[str] = None

    # ──────────────── setup ────────────────

    def initialize_pool(self, agent_factory: Optional[Callable] = None,
                         num_agents: int = 5) -> int:
        """Ensure the pool has at least ``num_agents`` agents (clamped
        to [3, 15]). Returns the number created."""
        num_agents = max(self.pool.min_agents,
                         min(self.pool.max_agents, num_agents))
        created = 0
        for _ in range(num_agents):
            ag = None
            if agent_factory is not None:
                ag = agent_factory()
            ma = self.pool.spawn(agent=ag)
            if ma is None:
                break  # pool full
            created += 1
        return created

    def get_commander_agent_id(self) -> str:
        """Reserve a special agent slot for the Commander itself.
        The Commander uses this ID when it steps in to do work."""
        if self._commander_agent_id is None:
            ma = self.pool.spawn(name="Commander", role="commander")
            if ma is not None:
                self._commander_agent_id = ma.id
            else:
                # Pool full — create a virtual ID.
                self._commander_agent_id = "commander_virtual"
        return self._commander_agent_id

    # ──────────────── phase 1: planning ────────────────

    def plan(self, goal: str,
             handler: Optional[Callable] = None) -> List[Task]:
        """Break the project goal into tasks and populate the TaskBoard.

        Uses the plan_handler (or injected handler) to call the LLM.
        Falls back to a single-task heuristic if no handler is
        configured (degraded mode).

        Returns the list of created Task objects.
        """
        self._record_decision("plan", {"goal": goal})

        run_handler = handler or self.plan_handler
        if run_handler is not None:
            try:
                raw = run_handler(
                    system_prompt=PLANNER_SYSTEM_PROMPT,
                    user_prompt=goal,
                )
                plan_items = _parse_plan(raw or "")
                if plan_items:
                    return self._populate_board(plan_items)
            except Exception as e:
                logger.warning("plan handler failed: %r", e)

        # Heuristic fallback: single task covering the whole goal.
        logger.info("plan: using heuristic fallback (no handler)")
        task = self.board.create_task(
            title=f"Implement: {goal[:80]}",
            description=goal,
            difficulty=TaskDifficulty.MEDIUM,
            resources=[],
            max_assignees=3,
        )
        return [task]

    def _populate_board(self, plan_items: List[Dict]) -> List[Task]:
        """Create Task objects from parsed plan items. Dependencies are
        resolved by title (first pass: create tasks, second pass: wire
        dependencies by title)."""
        title_to_id: Dict[str, str] = {}
        created: List[Task] = []

        # First pass: create tasks, collect title -> id mapping.
        for item in plan_items:
            task = self.board.create_task(
                title=item["title"],
                description=item["description"],
                difficulty=item.get("difficulty", 5),
                resources=item.get("resources", []),
                max_assignees=3,
            )
            title_to_id[item["title"]] = task.id
            created.append(task)

        # Second pass: wire dependencies by title.
        for item in plan_items:
            task_id = title_to_id.get(item["title"])
            if not task_id:
                continue
            dep_ids = [title_to_id.get(d, d)
                        for d in item.get("dependencies", [])
                        if title_to_id.get(d, d) is not None]
            # Update the task's dependencies in place.
            task = self.board.get_task(task_id)
            if task:
                # We need to mutate through the board's lock — but
                # since Task.dependencies is a plain list, direct
                # assignment works. The board's add_task already
                # stored it; we just update the field.
                task.dependencies = dep_ids

        return created

    # ──────────────── phase 2: initial dispatch ────────────────

    def initial_dispatch(self) -> Dict[str, Any]:
        """Assign assignable tasks to idle agents. Returns a summary
        of what was dispatched."""
        assignable = self.board.get_assignable_tasks()
        idle_agents = self.pool.list_idle()
        dispatch_log: List[Dict] = []

        for task in sorted(assignable, key=lambda t: -t.difficulty):
            if not idle_agents:
                break
            # Try to assign 1–2 agents to high-difficulty tasks.
            agents_to_assign = min(
                2 if task.difficulty >= TaskDifficulty.HARD else 1,
                len(idle_agents),
                task.max_assignees - len(task.assigned_agents),
            )
            for _ in range(agents_to_assign):
                if not idle_agents:
                    break
                agent = idle_agents.pop(0)
                if self._assign_with_claims(agent.id, task):
                    dispatch_log.append({
                        "agent_id": agent.id,
                        "task_id": task.id,
                        "task_title": task.title,
                        "difficulty": task.difficulty,
                    })

        self._record_decision("dispatch", {"assignments": dispatch_log})
        return {"dispatched": len(dispatch_log), "assignments": dispatch_log}

    def _assign_with_claims(self, agent_id: str, task: Task) -> bool:
        """Assign an agent to a task AND claim its resources atomically.
        If resource claims conflict, the assignment is rolled back."""
        if not task.resources:
            self.board.assign_agent(task.id, agent_id)
            self.pool.assign(agent_id, task.id)
            return True

        result, conflicts = self.conflict_guard.claim_batch(
            task.resources, task.id, agent_id,
        )
        if result == ClaimResult.CONFLICT:
            logger.info(
                "assign_with_claims: task %s resources conflict: %s",
                task.id, conflicts,
            )
            return False

        self.board.assign_agent(task.id, agent_id)
        self.pool.assign(agent_id, task.id)
        return True

    # ──────────────── phase 3: tick (coordination cycle) ────────────────

    def tick(self) -> Dict[str, Any]:
        """One coordination cycle. This is the Commander's main loop:
          1. Redirect idle agents to the hardest open task.
          2. Execute work on all assigned-but-unworked tasks.
          3. Thin overstaffed tasks.
          4. Handle failed agents.
          5. Submit completed tasks for review.

        Returns a summary of actions taken.
        """
        actions: List[Dict] = []

        # 1. Redirect idle agents.
        redirect_actions = self._redirect_idle_agents()
        actions.extend(redirect_actions)

        # 2. Execute work on each assigned task that hasn't been done yet.
        work_actions = self._execute_work()
        actions.extend(work_actions)

        # 3. Thin overstaffed tasks.
        thin_actions = self._thin_overstaffed_tasks()
        actions.extend(thin_actions)

        # 4. Handle failures.
        fail_actions = self._handle_failures()
        actions.extend(fail_actions)

        # 5. Process review queue.
        review_actions = self._process_review_queue()
        actions.extend(review_actions)

        return {
            "cycle_actions": len(actions),
            "actions": actions,
            "board_summary": self.board.summary(),
            "pool_summary": self.pool.summary(),
        }

    def _execute_work(self) -> List[Dict]:
        """The Commander steps in to execute work on every task that has
        agents assigned but no result yet. This is how actual LLM work
        gets done — the Commander calls work_on_task() for each pending
        task, which uses the work_handler (LLM) to produce a result.
        """
        actions: List[Dict] = []
        for task in self.board.list_all():
            if task.status.value not in ("pending", "assigned"):
                continue
            if not task.assigned_agents:
                continue
            # Execute work on this task
            try:
                result = self.work_on_task(task.id)
                if result:
                    actions.append({
                        "action": "execute_work",
                        "task_id": task.id,
                        "task_title": task.title,
                        "result_preview": result[:80],
                    })
                else:
                    actions.append({
                        "action": "execute_work_no_result",
                        "task_id": task.id,
                        "task_title": task.title,
                    })
            except Exception as e:
                logger.warning("execute_work failed for %s: %r", task.id, e)
                actions.append({
                    "action": "execute_work_failed",
                    "task_id": task.id,
                    "error": str(e),
                })
        return actions

    def _redirect_idle_agents(self) -> List[Dict]:
        """Find idle agents and redirect them to the hardest open
        task that can accept more workers. Also notify existing
        workers on that task that a helper is incoming."""
        actions: List[Dict] = []
        idle = self.pool.list_idle()
        if not idle:
            return actions

        for agent in idle:
            hardest = self.board.get_hardest_open_task()
            if hardest is None:
                # No open tasks — agent stays idle.
                break

            if self._assign_with_claims(agent.id, hardest):
                existing = self.board.get_tasks_for_agent(agent.id)
                # Notify existing workers on the hardest task.
                existing_workers = self.pool.list_working_on(hardest.id)
                for w in existing_workers:
                    if w.id != agent.id:
                        self._notify(
                            agent_id=w.id,
                            message=(
                                f"New agent {agent.id} is joining your "
                                f"task '{hardest.title}' to help. "
                                f"Coordinate with them — avoid editing "
                                f"the same files simultaneously. Your "
                                f"resource claims are protected by the "
                                f"ConflictGuard."
                            ),
                        )
                actions.append({
                    "action": "redirect_idle",
                    "agent_id": agent.id,
                    "to_task": hardest.id,
                    "task_title": hardest.title,
                    "difficulty": hardest.difficulty,
                    "notified_workers": len(existing_workers),
                })
            else:
                # Resource conflict — agent stays idle for now.
                actions.append({
                    "action": "redirect_blocked_conflict",
                    "agent_id": agent.id,
                    "task_id": hardest.id,
                    "reason": "resource claims conflict with active task",
                })

        self._record_decision("redirect", {"actions": actions})
        return actions

    def _thin_overstaffed_tasks(self) -> List[Dict]:
        """Pull the newest-joined agent off any task that has exceeded
        its max_assignees cap. The pulled agent becomes idle and will
        be redirected by _redirect_idle_agents on the next tick."""
        actions: List[Dict] = []
        overstaffed = self.board.get_overstaffed_tasks()
        for task in overstaffed:
            if not task.assigned_agents:
                continue
            # Pull the last-joined agent (end of the list).
            pulled_id = task.assigned_agents[-1]
            self.board.unassign_agent(task.id, pulled_id)
            self.conflict_guard.release_for_agent(task.id, pulled_id)
            self.pool.release(pulled_id)
            actions.append({
                "action": "thin_overstaffed",
                "task_id": task.id,
                "pulled_agent": pulled_id,
                "remaining_workers": len(task.assigned_agents),
            })
        self._record_decision("thin", {"actions": actions})
        return actions

    def _handle_failures(self) -> List[Dict]:
        """Handle failed agents. For each failed agent:
          - Release its resource claims.
          - If its task still has other workers, let them absorb.
          - If its task is now orphaned, the Commander decides:
            (a) step in directly (self_work),
            (b) pair-up two idle agents,
            (c) mark the task as failed.
        """
        actions: List[Dict] = []
        failed_agents = self.pool.list_failed()
        for ma in failed_agents:
            # Find the task this agent was on (if any).
            old_task_id = ma.current_task_id
            if old_task_id:
                self.conflict_guard.release_for_agent(
                    old_task_id, ma.id,
                )
                task = self.board.get_task(old_task_id)
                if task and task.assigned_agents:
                    # Other workers can absorb — notify them.
                    for w in self.pool.list_working_on(old_task_id):
                        self._notify(
                            agent_id=w.id,
                            message=(
                                f"Agent {ma.id} failed while working on "
                                f"'{task.title}'. Please absorb their "
                                f"portion of the work. Freed resources "
                                f"are now available for claiming."
                            ),
                        )
                    actions.append({
                        "action": "fail_absorbed",
                        "failed_agent": ma.id,
                        "task_id": old_task_id,
                        "absorbed_by": [w.id for w in
                                        self.pool.list_working_on(old_task_id)],
                    })
                elif task and not task.assigned_agents:
                    # Task is orphaned — Commander decides.
                    idle = self.pool.list_idle()
                    if len(idle) >= 2:
                        # Pair-up: send two idle agents.
                        a1, a2 = idle[0], idle[1]
                        self._assign_with_claims(a1.id, task)
                        self._assign_with_claims(a2.id, task)
                        actions.append({
                            "action": "fail_pair_up",
                            "failed_agent": ma.id,
                            "task_id": old_task_id,
                            "new_workers": [a1.id, a2.id],
                        })
                    elif len(idle) == 1:
                        # Single replacement.
                        self._assign_with_claims(idle[0].id, task)
                        actions.append({
                            "action": "fail_single_replace",
                            "failed_agent": ma.id,
                            "task_id": old_task_id,
                            "replacement": idle[0].id,
                        })
                    else:
                        # No idle agents — Commander steps in.
                        cid = self.get_commander_agent_id()
                        if cid != "commander_virtual":
                            self._assign_with_claims(cid, task)
                            actions.append({
                                "action": "fail_commander_steps_in",
                                "failed_agent": ma.id,
                                "task_id": old_task_id,
                                "commander_agent_id": cid,
                            })
                        else:
                            # Can't step in — mark task failed.
                            self.board.fail_task(
                                old_task_id, ma.id,
                                f"agent failed and no replacement available",
                            )
                            actions.append({
                                "action": "fail_task_orphaned",
                                "failed_agent": ma.id,
                                "task_id": old_task_id,
                            })

            # Reset the failed agent back to idle (Commander's choice
            # to retry rather than retire — the spec says the
            # Commander "can go replace it or not"). We choose: reset
            # so it can be reused, unless it has too many errors.
            if ma.total_errors < 3:
                self.pool.reset_agent(ma.id)
                actions.append({
                    "action": "fail_agent_reset",
                    "agent_id": ma.id,
                    "reason": "errors under threshold, retrying",
                })
            else:
                self.pool.retire(ma.id)
                actions.append({
                    "action": "fail_agent_retired",
                    "agent_id": ma.id,
                    "reason": "too many errors",
                })

        self._record_decision("fail_handle", {"actions": actions})
        return actions

    def _process_review_queue(self) -> List[Dict]:
        """Submit tasks in IN_REVIEW status to the Reviewer and act
        on the result."""
        actions: List[Dict] = []
        pending_review = self.board.get_pending_review()
        for task in pending_review:
            review = self.reviewer.review_task(
                task_title=task.title,
                task_description=task.description,
                result=task.result or "",
            )
            if review.approved:
                self.board.approve_task(task.id, review.feedback)
                # Release resource claims for completed task.
                self.conflict_guard.release_for_task(task.id)
                # Mark assignees as idle.
                for aid in list(task.assigned_agents):
                    self.pool.mark_completed(aid, task.id)
                actions.append({
                    "action": "review_approved",
                    "task_id": task.id,
                    "feedback": review.feedback[:100],
                })
            else:
                self.board.reject_task(
                    task.id, review.feedback, reassign=True,
                )
                actions.append({
                    "action": "review_rejected",
                    "task_id": task.id,
                    "feedback": review.feedback[:100],
                    "concerns": review.concerns[:3],
                })
        self._record_decision("review", {"actions": actions})
        return actions

    # ──────────────── phase 4: Commander does work ────────────────

    def work_on_task(self, task_id: str,
                     handler: Optional[Callable] = None) -> Optional[str]:
        """The Commander steps in to do work on a task directly.
        Uses agent_0 (the Commander's own agent slot in the pool).

        This is the spec's "总指挥也能拉去干活的" — even the
        Commander can be pulled into real work on complex projects.
        """
        task = self.board.get_task(task_id)
        if task is None:
            return None

        cid = self.get_commander_agent_id()
        self._assign_with_claims(cid, task)

        run_handler = handler or self.work_handler
        if run_handler is not None:
            try:
                result = run_handler(
                    system_prompt=COMMANDER_SYSTEM_PROMPT,
                    user_prompt=task.description,
                )
                self.board.submit_result(task_id, cid, result or "")
                self._record_decision("self_work", {
                    "task_id": task_id, "result_preview": (result or "")[:100],
                })
                return result
            except Exception as e:
                logger.warning("commander work handler failed: %r", e)
                self.board.fail_task(task_id, cid, str(e))
                return None
        else:
            # No handler — just mark the task as having the Commander
            # assigned (the actual work is done by the caller).
            self._record_decision("self_work", {
                "task_id": task_id, "note": "no handler, assignment only",
            })
            return None

    # ──────────────── phase 5: run to completion ────────────────

    def run_to_completion(self, max_cycles: int = 100) -> Dict[str, Any]:
        """Loop tick() until all tasks are COMPLETED or FAILED, or
        until max_cycles is reached. Returns a final summary."""
        cycles = 0
        for _ in range(max_cycles):
            cycles += 1
            self.tick()
            summary = self.board.summary()
            by_status = summary["by_status"]
            open_count = (by_status.get("pending", 0)
                          + by_status.get("assigned", 0)
                          + by_status.get("in_review", 0)
                          + by_status.get("blocked", 0))
            if open_count == 0:
                break
        return {
            "cycles": cycles,
            "final_board": self.board.summary(),
            "final_pool": self.pool.summary(),
            "decisions": len(self._decisions),
            "notifications_sent": len(self._notifications),
        }

    # ──────────────── helpers ────────────────

    def _notify(self, agent_id: str, message: str) -> None:
        """Send a notification to an agent. In a real deployment this
        would inject into the agent's conversation context; here we
        just log it for the audit trail."""
        self._notifications.append({
            "agent_id": agent_id,
            "message": message,
            "timestamp": time.time(),
        })
        logger.info("notify %s: %s", agent_id, message[:80])

    def _record_decision(self, decision_type: str,
                          details: Dict) -> None:
        self._decisions.append(CommanderDecision(
            decision_type=decision_type, details=details,
        ))

    def get_notifications(self, agent_id: Optional[str] = None) -> List[Dict]:
        if agent_id is None:
            return list(self._notifications)
        return [n for n in self._notifications
                if n["agent_id"] == agent_id]

    def get_decision_log(self) -> List[CommanderDecision]:
        return list(self._decisions)

    def get_anti_loop_signals(self, agent_id: str) -> Optional[Dict]:
        """Get the current anti-loop state for an agent (for
        Commander-level monitoring)."""
        ma = self.pool.get(agent_id)
        if ma is None:
            return None
        return ma.loop_guardian.snapshot()

    def inject_loop_steering(self, agent_id: str,
                              signal: LoopSignal) -> str:
        """When an agent's anti-loop signal escalates, the Commander
        produces a steering prompt to inject. This is the spec's
        '大量提示词' approach — we guide the agent out of the loop
        via prompts, not by killing it."""
        if not signal.should_inject:
            return ""
        steering = (
            f"[Commander steering] Agent {agent_id} is showing loop "
            f"behavior on tool '{signal.tool_name}' "
            f"(repeated {signal.repeated_count}×). "
            f"{signal.injection}"
        )
        self._notify(agent_id, steering)
        return steering

    def summary(self) -> Dict[str, Any]:
        return {
            "commander_id": self.commander_id,
            "commander_agent_id": self._commander_agent_id,
            "board": self.board.summary(),
            "pool": self.pool.summary(),
            "reviewer": self.reviewer.summary(),
            "total_decisions": len(self._decisions),
            "total_notifications": len(self._notifications),
        }
