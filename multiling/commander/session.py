"""
session.py — CommanderSession: the user-facing wrapper that ties
everything together.

CommanderSession is the entry point a caller uses to run a project
under the Commander framework. It wires up:
  - CommanderAI (the orchestrator)
  - TaskBoard, AgentPool, ConflictGuard, Reviewer
  - An injectable execution handler (defaults to the real
    SubagentPool-based handler from tools/delegate_tool.py)

Usage::

    session = CommanderSession(goal="Build a REST API")
    session.setup(num_agents=5)
    session.plan()
    result = session.run()

Or with an injected handler for testing::

    def fake_handler(system_prompt, user_prompt):
        return '{"approved": true, "feedback": "ok"}'

    session = CommanderSession(
        goal="test", plan_handler=fake_handler,
        work_handler=fake_handler,
    )
    session.setup(num_agents=3)
    session.plan()
    result = session.run()
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .commander import CommanderAI
from .task_board import TaskBoard, TaskStatus
from .agent_pool import AgentPool
from .conflict_guard import ConflictGuard
from .reviewer import Reviewer

logger = logging.getLogger(__name__)


class CommanderSession:
    """User-facing session wrapper for the Commander framework.

    This is the high-level API:
      1. ``setup()``    — initialize the agent pool.
      2. ``plan()``     — break the goal into tasks.
      3. ``run()``      — run the Commander loop to completion.
      4. ``summary()``  — get the final report.
    """

    def __init__(self,
                 goal: str,
                 plan_handler: Optional[Callable] = None,
                 work_handler: Optional[Callable] = None,
                 review_handler: Optional[Callable] = None,
                 agent_factory: Optional[Callable] = None,
                 num_agents: int = 5):
        self.goal = goal
        self.board = TaskBoard()
        self.pool = AgentPool()
        self.conflict_guard = ConflictGuard()
        self.reviewer = Reviewer(handler=review_handler)
        self.commander = CommanderAI(
            task_board=self.board,
            agent_pool=self.pool,
            conflict_guard=self.conflict_guard,
            reviewer=self.reviewer,
            plan_handler=plan_handler,
            work_handler=work_handler,
        )
        self._agent_factory = agent_factory
        self._num_agents = num_agents
        self._planned = False
        self._result: Optional[Dict] = None
        self._started_at: Optional[float] = None

    def setup(self, num_agents: Optional[int] = None) -> int:
        """Initialize the agent pool with ``num_agents`` agents."""
        n = num_agents or self._num_agents
        return self.commander.initialize_pool(
            agent_factory=self._agent_factory, num_agents=n,
        )

    def plan(self) -> List:
        """Break the goal into tasks and populate the task board."""
        tasks = self.commander.plan(self.goal)
        self._planned = True
        return tasks

    def run(self, max_cycles: int = 50) -> Dict[str, Any]:
        """Run the Commander coordination loop to completion.

        Pre-condition: ``setup()`` and ``plan()`` have been called.
        If not, ``setup()`` is called automatically; ``plan()``
        must be called explicitly (or via ``plan_and_run()``).
        """
        if self.pool.size() == 0:
            self.setup()
        if not self._planned:
            self.plan()

        self._started_at = time.time()

        # Initial dispatch.
        dispatch_result = self.commander.initial_dispatch()
        logger.info("initial dispatch: %s", dispatch_result)

        # Run to completion.
        run_result = self.commander.run_to_completion(max_cycles=max_cycles)
        self._result = run_result
        return run_result

    def plan_and_run(self, max_cycles: int = 50) -> Dict[str, Any]:
        """Convenience: plan + run in one call."""
        self.plan()
        return self.run(max_cycles=max_cycles)

    def summary(self) -> Dict[str, Any]:
        """Final summary of the session."""
        if self._result is None:
            return {"status": "not_run"}
        elapsed = (self._result.get("cycles", 0)
                   if self._started_at is None
                   else round(time.time() - self._started_at, 2))
        return {
            "goal": self.goal,
            "elapsed_sec": elapsed,
            "cycles": self._result.get("cycles", 0),
            "board": self.board.summary(),
            "pool": self.pool.summary(),
            "reviewer": self.reviewer.summary(),
            "decisions_made": len(self.commander.get_decision_log()),
            "notifications_sent": len(self.commander.get_notifications()),
            "tasks": [t.to_dict() for t in self.board.list_all()],
        }

    def get_commander(self) -> CommanderAI:
        """Direct access to the Commander for advanced use."""
        return self.commander

    def get_board(self) -> TaskBoard:
        return self.board

    def get_pool(self) -> AgentPool:
        return self.pool

    def get_conflict_guard(self) -> ConflictGuard:
        return self.conflict_guard

    def get_reviewer(self) -> Reviewer:
        return self.reviewer

    def reset(self) -> None:
        """Reset the session for a fresh run (keeps the same goal
        and handlers)."""
        self.board = TaskBoard()
        self.pool = AgentPool()
        self.conflict_guard = ConflictGuard()
        self.reviewer = Reviewer(handler=self.reviewer.handler)
        self.commander = CommanderAI(
            task_board=self.board,
            agent_pool=self.pool,
            conflict_guard=self.conflict_guard,
            reviewer=self.reviewer,
            plan_handler=self.commander.plan_handler,
            work_handler=self.commander.work_handler,
        )
        self._planned = False
        self._result = None
        self._started_at = None
