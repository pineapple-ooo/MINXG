"""
council.py — CommanderCouncil: manages up to 2 CommanderAI instances.

The spec says: CommanderCouncil wraps 1-2 CommanderAI instances, providing:
  - Load-balanced task distribution across commanders
  - Coordinated agent pool sharing
  - Unified status view
  - Failover: if commander 1 is busy, commander 2 takes over

Also includes:
  - HighLevelOrchestrator (legacy)
  - CompanyOrchestrator (company-mode multi-agent with communication bus)
  - AgentGroup, model scoring, web search for models
"""

from __future__ import annotations

import json as _json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from multiling.constants import MAX_CONTEXT_CHARS

from .commander import CommanderAI
from .task_board import TaskBoard, Task, TaskStatus
from .agent_pool import AgentPool, ManagedAgent, AgentState
from .conflict_guard import ConflictGuard
from .reviewer import Reviewer

logger = logging.getLogger(__name__)

MAX_COMMANDERS = 2
RUNNING = True  # global flag for ultra-long tasks, set to False to stop gracefully


# ═══════════════════════════════════════════════════════════════════
#  CommanderCouncil
# ═══════════════════════════════════════════════════════════════════


class CommanderCouncil:
    """Manages up to 2 CommanderAI instances."""

    def __init__(self,
                 plan_handler: Optional[Callable] = None,
                 work_handler: Optional[Callable] = None,
                 review_handler: Optional[Callable] = None):
        self.commanders: List[CommanderAI] = []
        self.plan_handler = plan_handler
        self.work_handler = work_handler
        self.review_handler = review_handler
        self._lock = threading.RLock()

    def init_commanders(self, num_commanders: int = 1,
                        agents_per: int = 5,
                        agent_factory: Optional[Callable] = None) -> int:
        num_commanders = max(1, min(MAX_COMMANDERS, num_commanders))
        total = 0
        for i in range(num_commanders):
            cmd = CommanderAI(
                plan_handler=self.plan_handler,
                work_handler=self.work_handler,
                reviewer=Reviewer(handler=self.review_handler),
                commander_id=f"commander_{i + 1}",
            )
            created = cmd.initialize_pool(
                agent_factory=agent_factory,
                num_agents=agents_per,
            )
            total += created
            self.commanders.append(cmd)
        return total

    def get_commander(self, idx: int = 0) -> Optional[CommanderAI]:
        if 0 <= idx < len(self.commanders):
            return self.commanders[idx]
        return None

    def get_least_loaded(self) -> CommanderAI:
        if not self.commanders:
            raise RuntimeError("No commanders initialized")
        return min(self.commanders,
                   key=lambda c: c.pool.get_working_count())

    def plan(self, goal: str,
             handler: Optional[Callable] = None) -> Dict[str, List[Task]]:
        result: Dict[str, List[Task]] = {}
        if len(self.commanders) == 1:
            tasks = self.commanders[0].plan(goal, handler=handler)
            result[self.commanders[0].commander_id] = tasks
        else:
            halves = self._split_goal(goal)
            for i, cmd in enumerate(self.commanders):
                if i < len(halves):
                    tasks = cmd.plan(halves[i], handler=handler)
                    result[cmd.commander_id] = tasks
        return result

    def _split_goal(self, goal: str) -> List[str]:
        if not self.plan_handler:
            import re
            sentences = re.split(r'(?<=[.!?])\s+', goal.strip())
            mid = max(1, len(sentences) // 2)
            left = " ".join(sentences[:mid]).strip()
            right = " ".join(sentences[mid:]).strip()
            if not right:
                return [goal, f"Continuation: {goal}"]
            return [left, right]
        return [goal, f"Continuation: {goal}"]

    def dispatch(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for cmd in self.commanders:
            results[cmd.commander_id] = cmd.initial_dispatch()
        return results

    def tick(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for cmd in self.commanders:
            r = cmd.tick()
            results[cmd.commander_id] = r
        return results

    def run(self, max_cycles: int = 50) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for cmd in self.commanders:
            results[cmd.commander_id] = cmd.run_to_completion(
                max_cycles=max_cycles)
        return results

    def get_status(self) -> Dict[str, Any]:
        cmd_summaries = []
        for cmd in self.commanders:
            cmd_summaries.append({
                "id": cmd.commander_id,
                "pool": cmd.pool.summary(),
                "board": cmd.board.summary(),
            })

        agents = []
        tasks = []
        for cmd in self.commanders:
            for ma in cmd.pool.list_all():
                agents.append({
                    "id": ma.id, "name": ma.name, "role": ma.role,
                    "state": str(ma.state), "current_task_id": ma.current_task_id,
                    "commander_id": cmd.commander_id, "idle_since": ma.idle_since,
                    "working_since": ma.working_since,
                    "total_tasks_completed": ma.total_tasks_completed,
                    "total_errors": ma.total_errors,
                })
            for t in cmd.board.list_all():
                tasks.append({
                    "id": t.id, "title": t.title, "difficulty": t.difficulty,
                    "status": str(t.status), "assigned_agents": list(t.assigned_agents),
                    "commander_id": cmd.commander_id,
                    "dependencies": list(t.dependencies),
                    "result": (t.result or "")[:100] if t.result else None,
                    "failure_reason": t.failure_reason,
                })

        total_agents = sum(s["pool"]["total_agents"] for s in cmd_summaries)
        working = sum(1 for a in agents if a["state"] == "working")
        idle = sum(1 for a in agents if a["state"] == "idle")
        failed = sum(1 for a in agents if a["state"] == "failed")
        total_tasks = len(tasks)
        completed = sum(1 for t in tasks if t["status"] == "completed")

        return {
            "summary": {
                "num_commanders": len(self.commanders),
                "total_agents": total_agents, "working": working,
                "idle": idle, "failed": failed,
                "total_tasks": total_tasks, "completed": completed,
            },
            "commanders": cmd_summaries, "agents": agents, "tasks": tasks,
        }


# ═══════════════════════════════════════════════════════════════════
#  HighLevelOrchestrator (legacy)
# ═══════════════════════════════════════════════════════════════════


class HighLevelOrchestrator:
    """High-level AI overseer for multi-agent collaboration (legacy)."""

    def __init__(self, llm_complete, plan_handler=None, work_handler=None):
        self.llm = llm_complete
        self.plan_handler = plan_handler
        self.work_handler = work_handler
        self.council: Optional[CommanderCouncil] = None
        self.result: Dict[str, Any] = {}

    def estimate_scale(self, goal: str) -> Dict[str, int]:
        prompt = (
            "You are a resource estimator for a multi-agent coding crew. "
            "Given a user's goal, decide how many commanders and agents "
            "are needed.\n\n"
            "Rules:\n"
            "- Small / single-file tasks: 1 commander, 3 agents.\n"
            "- Medium / multi-file tasks: 1 commander, 5 agents.\n"
            "- Large / multi-module tasks: 2 commanders, 5 agents each.\n\n"
            f"Goal: {goal}\n\n"
            "Reply with ONLY a JSON object: "
            '{"commanders": 1, "agents_per": 5}'
        )
        try:
            raw = self.llm(system_prompt="", user_prompt=prompt) if self.llm else ""
            if raw:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    parsed = _json.loads(raw[start:end])
                    return {
                        "commanders": max(1, min(2, parsed.get("commanders", 1))),
                        "agents_per": max(3, min(10, parsed.get("agents_per", 5))),
                    }
        except Exception:
            pass
        return {"commanders": 1, "agents_per": 5}

    def run(self, goal: str) -> Dict[str, Any]:
        scale = self.estimate_scale(goal)
        self.council = CommanderCouncil(
            plan_handler=self.plan_handler or self.llm,
            work_handler=self.work_handler or self.llm,
        )
        total_agents = self.council.init_commanders(
            num_commanders=scale["commanders"],
            agents_per=scale["agents_per"],
        )
        plans = self.council.plan(goal)
        total_tasks = sum(len(tasks) for tasks in plans.values())
        result = self.council.run(max_cycles=50)

        task_results = []
        for cmd in self.council.commanders:
            for t in cmd.board.list_all():
                task_results.append({
                    "id": t.id, "title": t.title,
                    "status": str(t.status.value),
                    "result": (t.result or "")[:200],
                    "difficulty": t.difficulty,
                })

        summary_prompt = (
            "You are a project manager closing a multi-agent collaboration. "
            "Write a brief completion statement (2-3 sentences) summarising "
            "what was achieved. Be positive and concrete.\n\n"
            f"Goal: {goal}\n"
            f"Commanders: {scale['commanders']}\n"
            f"Agents: {total_agents}\n"
            f"Tasks: {total_tasks}\n"
            f"Completed: {sum(1 for t in task_results if t['status'] == 'completed')}\n"
            "Completion statement:"
        )
        completion = ""
        try:
            if self.llm:
                completion = self.llm(system_prompt="", user_prompt=summary_prompt)
        except Exception:
            completion = ""

        self.result = {
            "goal": goal, "scale": scale, "total_agents": total_agents,
            "total_tasks": total_tasks, "task_results": task_results,
            "council_result": result,
            "completion": completion or "Multi-agent collaboration complete.",
            "status": "completed",
        }
        return self.result


# ═══════════════════════════════════════════════════════════════════
#  AgentGroup, model scoring, web search
# ═══════════════════════════════════════════════════════════════════


class AgentGroup:
    """An agent group with role, model, and output."""

    def __init__(self, name: str, role: str, model: str, agent_count: int):
        self.name = name
        self.role = role
        self.model = model
        self.agent_count = agent_count
        self.output: str = ""


def _score_model(name: str) -> float:
    """Score a model: higher version = stronger, more creative name = stronger."""
    score = 0.0
    lower = name.lower()
    import re as _re
    versions = _re.findall(r'(?:v?)(\d+)(?:\.(\d+))?(?:\.(\d+))?', lower)
    for v in versions:
        major = int(v[0]) if v[0] else 0
        minor = int(v[1]) if v[1] else 0
        patch = int(v[2]) if v[2] else 0
        score += major * 10 + minor * 3 + patch * 1
    keywords = {
        "opus": 20, "sonnet": 15, "haiku": 10,
        "pro": 12, "ultra": 15, "max": 14,
        "flash": 8, "lite": 5, "mini": 3,
        "nova": 18, "stellar": 16, "cosmos": 18,
        "maverick": 14, "scout": 10,
        "reasoning": 8, "thinking": 6,
    }
    for kw, pts in keywords.items():
        if kw in lower:
            score += pts
    special = sum(1 for c in name if not c.isalnum() and c not in "./")
    score += special * 2
    if len(name) > 20:
        score += 5
    if len(name) > 30:
        score += 5
    return score


def _rank_models(models: List[str]) -> List[Tuple[str, float]]:
    scored = [(m, _score_model(m)) for m in models]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _search_web_for_models(query: str) -> str:
    """Free local search for model info using DuckDuckGo (no API key)."""
    import urllib.request, urllib.parse
    try:
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query + ' AI model')}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        import re as _re
        results = []
        for match in _re.finditer(r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, _re.DOTALL):
            url = match.group(1)
            title = _re.sub(r'<[^>]+>', '', match.group(2)).strip()
            results.append(f"{title}: {url}")
        for match in _re.finditer(r'<a[^>]*href="([^"]*)"[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</a>', html, _re.DOTALL):
            url = match.group(1)
            title = _re.sub(r'<[^>]+>', '', match.group(2)).strip()
            if title and url and title not in [r.split(": ")[0] for r in results]:
                results.append(f"{title}: {url}")
        if results:
            return "\n".join(results[:8])
        return f"No results. Try: https://www.google.com/search?q={urllib.parse.quote(query)}"
    except Exception as e:
        return f"Search unavailable: {e}"


# ═══════════════════════════════════════════════════════════════════
#  CompanyOrchestrator — company-mode multi-agent with communication bus
# ═══════════════════════════════════════════════════════════════════


class CompanyOrchestrator:
    """Company-mode multi-agent orchestration with communication bus.

    Structure:
      CEO (user's AI) — top-level decision maker
      +-- Managing Director — coordinates groups, authority below CEO
      +-- Architecture Group — designs plan
      +-- Worker Group — writes code
      +-- Testing Group — writes tests
      +-- Art Group — UI/design
      +-- Sarcasm Group — bug review

    All groups can use tools (file, execute, git, platform, reverse).
    Communication bus enables agent-to-agent messaging and status sharing.
    Supports ultra-long tasks (10+ days) via checkpoints and persistence.
    """

    GROUPS = [
        ("Architecture", "architecture"),
        ("Worker", "worker"),
        ("Testing", "testing"),
        ("Art", "art"),
        ("Sarcasm", "sarcasm"),
    ]

    GROUP_MODEL_WEIGHT = {
        "worker": 1.0, "architecture": 0.9, "testing": 0.8,
        "art": 0.7, "sarcasm": 0.5,
    }

    def __init__(self, llm_complete, llm_with_model, fetch_models_fn):
        self.llm = llm_complete
        self.llm_with_model = llm_with_model
        self.fetch_models_fn = fetch_models_fn
        self.groups: Dict[str, AgentGroup] = {}
        self.available_models: List[str] = []
        self.ranked_models: List[Tuple[str, float]] = []
        self.result: Dict[str, Any] = {}

    def _select_models(self, total_agents: int) -> Dict[str, str]:
        ranked = self.ranked_models
        if not ranked:
            return {g: "" for g, _ in self.GROUPS}
        assigned: Dict[str, str] = {}
        used = set()
        for group_key, _ in self.GROUPS:
            weight = self.GROUP_MODEL_WEIGHT.get(group_key, 0.5)
            idx = max(0, min(len(ranked) - 1,
                            int((1.0 - weight) * len(ranked) * 0.5)))
            for i in range(idx, len(ranked)):
                m = ranked[i][0]
                if m not in used:
                    assigned[group_key] = m
                    used.add(m)
                    break
            if group_key not in assigned:
                for m, _ in ranked:
                    if m not in used:
                        assigned[group_key] = m
                        used.add(m)
                        break
        return assigned

    def _get_reasoning_effort(self, model_name: str) -> str:
        """Get a model's supported reasoning effort level."""
        if not model_name:
            return "high"
        lower = model_name.lower()
        if any(k in lower for k in ("deepseek-r1", "deepseek-reasoner", "o3", "o4")):
            return "high"
        if any(k in lower for k in ("deepseek-v4", "gpt-5", "claude")):
            return "high"
        if "gemini" in lower:
            return "high"
        try:
            result = _search_web_for_models(f"{model_name} reasoning effort levels")
            if "xhigh" in result.lower():
                return "xhigh"
            if "high" in result.lower():
                return "high"
            if "medium" in result.lower():
                return "medium"
            if "low" in result.lower():
                return "low"
        except Exception:
            pass
        return "high"

    def _pick_alternate_model(self, group_key: str, current_model: str) -> Optional[str]:
        """Pick an alternate model from available models."""
        for m, _ in self.ranked_models:
            if m != current_model and m not in [g.model for g in self.groups.values()]:
                return m
        return None

    def estimate_scale(self, goal: str) -> Dict[str, Any]:
        search_results = _search_web_for_models(goal)
        prompt = (
            "You are the CEO of an AI development company. "
            "Given a user's goal, decide how many AI agents (3-50) "
            "are needed to complete it.\n\n"
            "Rules:\n"
            "- Simple / single-file tasks: 3-5 agents\n"
            "- Medium / multi-file tasks: 6-15 agents\n"
            "- Complex / multi-module tasks: 16-30 agents\n"
            "- Very large projects: 31-50 agents\n\n"
            "Agents are divided into 5 groups:\n"
            "  1. Architecture — designs the plan\n"
            "  2. Worker — writes code\n"
            "  3. Testing — writes tests\n"
            "  4. Art — UI design, visuals, images\n"
            "  5. Sarcasm — provides sarcastic review\n\n"
            f"Goal: {goal}\n\n"
            f"Web search results for context:\n{search_results[:1000]}\n\n"
            "Reply with ONLY a JSON object like:\n"
            '{"total_agents": 10, "reasoning": "brief justification"}'
        )
        try:
            raw = self.llm(system_prompt="", user_prompt=prompt)
            if raw:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    parsed = _json.loads(raw[start:end])
                    total = max(3, min(50, parsed.get("total_agents", 10)))
                    return {"total_agents": total, "reasoning": parsed.get("reasoning", "")}
        except Exception:
            pass
        return {"total_agents": 10, "reasoning": "default fallback"}

    def run(self, goal: str, reasoning_effort: Optional[str] = None) -> Dict[str, Any]:
        """Full lifecycle: scout → estimate → fetch → assign → MD → execute → finalize.

        Supports ultra-long tasks (10+ days) via checkpoint persistence and
        communication bus for agent coordination.
        """
        from .comm_bus import get_bus, AgentMessage

        bus = get_bus()
        bus.enable_checkpoint(os.path.expanduser("~/.minxg/comm_bus_checkpoint.json"))

        _ctx_parts: list[str] = [f"# Goal\n{goal}\n"]
        group_outputs: Dict[str, str] = {}

        # Dynamic context compression threshold
        _model_context_limits = {
            "gpt-5": 128000, "gpt-5-mini": 128000, "gpt-4o": 128000, "gpt-4o-mini": 128000,
            "claude-sonnet-4": 200000, "claude-opus-4": 200000, "claude-haiku-4": 200000,
            "claude-3-5-sonnet": 200000, "claude-3-5-haiku": 200000,
            "gemini-2.5-pro": 1000000, "gemini-2.5-flash": 1000000, "gemini-2.0-flash": 1000000,
            "gemini-1.5-pro": 2000000, "gemini-1.5-flash": 1000000,
            "deepseek-v4": 128000, "deepseek-r1": 128000, "deepseek-chat": 128000,
            "grok-4": 1000000, "minimax-m2": 1000000, "minimax-m2.7": 1000000,
            "qwen3-max": 128000, "qwen3-plus": 128000, "kimi-latest": 128000,
            "glm-4-plus": 128000, "glm-5": 192000, "glm-5.2": 256000,
            "mistral-large": 128000, "codestral": 256000,
            "llama-4-maverick": 128000, "llama-3.3-70b": 128000,
        }

        def _get_model_context_limit(model_name: str) -> int:
            if not model_name:
                return 128000
            lower = model_name.lower()
            for key, limit in _model_context_limits.items():
                if key in lower:
                    return limit
            import re as _re
            search_result = _search_web_for_models(f"{model_name} context window size tokens")
            match = _re.search(r'(\d+)[kK]', search_result)
            if match:
                return int(match.group(1)) * 1000
            return 128000

        def _compress_ctx(label: str = ""):
            ctx_str = "".join(_ctx_parts)
            if len(ctx_str) <= max_context_chars:
                return
            lines = ctx_str.split("\n")
            if len(lines) <= 20:
                return
            head = "\n".join(lines[:10])
            tail = "\n".join(lines[-10:])
            _ctx_parts.clear()
            _ctx_parts.append(f"{head}\n\n... [Context compressed - {label} - {len(lines)} lines -> 20] ...\n\n{tail}\n")

        def _report_status():
            snapshot = bus.get_latest_status_snapshot()
            alive = len(snapshot.get("active_agents", []))
            msgs = snapshot.get("total_messages", 0)
            logger.info("[Status] %d agents alive, %d messages on bus", alive, msgs)
            return snapshot

        # 0. CEO Scout
        scout_prompt = (
            "You are the CEO. Before assigning resources, SCOUT the task.\n"
            "Analyze:\n1. What needs to be changed or created\n"
            "2. Which groups are needed\n"
            "3. How complex the task really is\n\n"
            f"Goal: {goal}\n\nOutput a brief scouting report (3-5 sentences)."
        )
        scout_report = ""
        try:
            scout_report = self.llm(system_prompt="", user_prompt=scout_prompt)
        except Exception:
            scout_report = "Scouting failed."
        _ctx_parts.append(f"## CEO Scouting Report\n{scout_report or 'N/A'}\n\n")

        # 1. CEO Estimate Scale
        scale = self.estimate_scale(goal)
        total_agents = scale["total_agents"]

        # 2. Fetch and rank models
        try:
            self.available_models = self.fetch_models_fn() or []
        except Exception:
            self.available_models = []
        self.ranked_models = _rank_models(self.available_models)

        # 3. CEO assigns models
        model_assignments = self._select_models(total_agents)
        if not model_assignments:
            for g, _ in self.GROUPS:
                model_assignments[g] = ""

        model_reasoning: Dict[str, str] = {}
        for g_key, model in model_assignments.items():
            if model:
                effort = reasoning_effort or self._get_reasoning_effort(model)
                model_reasoning[model] = effort or ""

        group_ratios = {"architecture": 0.2, "worker": 0.4,
                        "testing": 0.2, "art": 0.1, "sarcasm": 0.1}
        for g_key, _ in self.GROUPS:
            count = max(1, int(total_agents * group_ratios.get(g_key, 0.2)))
            model = model_assignments.get(g_key, "")
            self.groups[g_key] = AgentGroup(
                name=dict(self.GROUPS)[g_key],
                role=g_key, model=model, agent_count=count,
            )

        # Register all groups on the communication bus
        for g_key, g in self.groups.items():
            agent_ids = [f"{g_key}-{i}" for i in range(g.agent_count)]
            bus.register_group(g.role, agent_ids)
            for aid in agent_ids:
                bus.update_agent_status(aid, {"state": "idle", "group": g.role, "model": g.model or "default"})

        # Compute dynamic context threshold
        all_models = [g.model for g in self.groups.values() if g.model] + ([self.available_models[0]] if self.available_models else [])
        limits = sorted(set(_get_model_context_limit(m) for m in all_models if m))
        if len(limits) >= 2:
            max_context_chars = int((limits[0] + limits[1]) / 2 * 0.25)
        elif limits:
            max_context_chars = int(limits[0] * 0.25)
        else:
            max_context_chars = MAX_CONTEXT_CHARS

        # 3.5 Managing Director
        md_prompt = (
            "You are the Managing Director. You REPORT TO the CEO. "
            "Your authority is LIMITED — coordinate the groups, do NOT make "
            "strategic decisions. The CEO has decided the resource allocation. "
            "Create a brief coordination plan based on the CEO's decisions.\n\n"
            "EMERGENCY PROTOCOL: When an agent calls EMERGENCY_CALL, you MUST:\n"
            "1. Acknowledge the emergency call\n"
            "2. Find another agent who can help\n"
            "3. Approve or reject the request\n"
            "4. Respond with EMERGENCY_RESPONSE: <caller> <approved/rejected> <reason>\n\n"
            f"CEO scouting: {scout_report[:500]}\n"
            f"Goal: {goal}\nTotal agents: {total_agents}\nModels: {model_assignments}\n"
            + "\n".join(f"  - {g.name}: {g.agent_count} agents, model={g.model or 'default'}"
                       for g_key, g in self.groups.items()) + "\n\n"
            "Output a coordination plan (2-3 sentences). Do NOT change the CEO's decisions."
        )
        md_output = ""
        try:
            md_output = self.llm(system_prompt="", user_prompt=md_prompt)
        except Exception:
            md_output = ""
        if md_output:
            _ctx_parts.append(f"## Managing Director Plan\n{md_output}\n\n")
        _compress_ctx("after MD")

        # 4. Workflow execution
        system_prompts = {
            "architecture": (
                "You are the Architecture Group. Design a detailed plan for the "
                "given goal. You have access to file and execute tools — use them "
                "to create design documents directly.\n\n"
                "EMERGENCY PROTOCOL: If you encounter a task you cannot handle alone, "
                "call for help by saying: EMERGENCY_CALL: <description of what you need help with>. "
                "Other groups will hear your call and the Managing Director will approve responders."
            ),
            "worker": (
                "You are the Worker Group. Write the actual code. Use the file, "
                "execute, and git tools to create, test, and commit code.\n\n"
                "EMERGENCY PROTOCOL: If you encounter a task you cannot handle alone, "
                "call for help by saying: EMERGENCY_CALL: <description of what you need help with>. "
                "Other groups will hear your call and the Managing Director will approve responders."
            ),
            "testing": (
                "You are the Testing Group. Write and run tests. Use the execute "
                "tool to run tests, file tool to create test files, and git tool "
                "to commit. Report any bugs found.\n\n"
                "EMERGENCY PROTOCOL: If you encounter a task you cannot handle alone, "
                "call for help by saying: EMERGENCY_CALL: <description of what you need help with>. "
                "Other groups will hear your call and the Managing Director will approve responders."
            ),
            "art": (
                "You are the Art Group. Design beautiful UI/UX, create visual "
                "assets. Use the file tool to create HTML/CSS/images. Focus on "
                "frontend aesthetics, color schemes, layouts, icons, and visuals.\n\n"
                "EMERGENCY PROTOCOL: If you encounter a task you cannot handle alone, "
                "call for help by saying: EMERGENCY_CALL: <description of what you need help with>. "
                "Other groups will hear your call and the Managing Director will approve responders."
            ),
            "sarcasm": (
                "You are the Sarcasm Group. Review the entire project and point "
                "out potential bugs, hidden issues, edge cases, and future problems. "
                "Use the file tool to read code and the execute tool to verify. "
                "Be sarcastic and brutally honest but make every criticism concrete.\n\n"
                "EMERGENCY PROTOCOL: If you encounter a task you cannot handle alone, "
                "call for help by saying: EMERGENCY_CALL: <description of what you need help with>. "
                "Other groups will hear your call and the Managing Director will approve responders."
            ),
        }

        for g_key, _ in self.GROUPS:
            if not RUNNING:
                _ctx_parts.append("\n[CEO] Received stop signal. Shutting down gracefully.\n")
                break

            group = self.groups[g_key]
            system_prompt = system_prompts.get(g_key, "")
            effort = model_reasoning.get(group.model or "", "")

            user_prompt = (
                f"{context}\n\nYour group ({group.name}) has {group.agent_count} agents. "
                f"Your assigned model: {group.model or 'default'}. "
                f"Reasoning effort: {effort or 'default'}. "
                "ALL groups can use tools (file, execute, git, platform, reverse). "
                "Use the communication bus to coordinate with other groups. "
                "Do your job and output the result."
            )

            for i in range(group.agent_count):
                bus.update_agent_status(f"{g_key}-{i}", {"state": "working", "task": "group execution"})

            _report_status()

            try:
                if group.model:
                    output = self.llm_with_model(
                        model=group.model, system_prompt=system_prompt, user_prompt=user_prompt,
                    )
                else:
                    output = self.llm(system_prompt=system_prompt, user_prompt=user_prompt)
                group.output = output or ""
                group_outputs[g_key] = group.output
                _ctx_parts.append(f"\n## {group.name} Output\n{group.output}\n\n")

                bus.post(AgentMessage(
                    sender=g_key, msg_type="info",
                    content=f"{group.name} completed. Output: {group.output[:200]}",
                ))

                # ── Emergency call handling ──
                if "EMERGENCY_CALL:" in (group.output or ""):
                    import re as _re
                    emergency_matches = _re.findall(r"EMERGENCY_CALL:\s*(.+?)(?:\n|$)", group.output)
                    for desc in emergency_matches:
                        caller_id = f"{g_key}-0"
                        bus.emergency_call(caller_id, desc.strip())
                        _ctx_parts.append(f"\n[EMERGENCY] {g_key} called for help: {desc.strip()}\n")
                        # Try to find a responder from another group
                        for other_key, other_group in self.groups.items():
                            if other_key != g_key and other_group.output:
                                if "EMERGENCY_RESPONSE:" in (other_group.output or ""):
                                    resp_match = _re.search(
                                        r"EMERGENCY_RESPONSE:\s*(\S+)\s+(approved|rejected)\s*(.*)",
                                        other_group.output,
                                    )
                                    if resp_match:
                                        responder = resp_match.group(1)
                                        decision = resp_match.group(2)
                                        reason = resp_match.group(3)
                                        bus.respond_to_emergency(responder, caller_id, decision, reason)
                                        _ctx_parts.append(f"[EMERGENCY] {responder} {decision} help for {g_key}: {reason}\n")
                                        break
                        else:
                            # Auto-approve from another available group
                            for other_key, other_group in self.groups.items():
                                if other_key != g_key and other_group.agent_count > 0:
                                    bus.respond_to_emergency(f"{other_key}-auto", caller_id, "approved",
                                                             f"Auto-assigned by {other_key}")
                                    _ctx_parts.append(f"[EMERGENCY] Auto-assigned {other_key} to help {g_key}\n")
                                    break

            except Exception as e:
                group.output = f"[{group.name} failed: {e}]"
                group_outputs[g_key] = group.output
                _ctx_parts.append(f"\n[CEO Signal] {group.name} died. CEO restarting with alternate model...\n")

                for i in range(group.agent_count):
                    bus.update_agent_status(f"{g_key}-{i}", {"state": "dead", "error": str(e)})

                alt_model = self._pick_alternate_model(g_key, model_assignments.get(g_key, ""))
                if alt_model:
                    _ctx_parts.append(f"[CEO] Switching {group.name} from {group.model or 'default'} to {alt_model}\n")
                    group.model = alt_model
                    try:
                        output = self.llm_with_model(
                            model=alt_model, system_prompt=system_prompt, user_prompt=user_prompt,
                        )
                        group.output = output or ""
                        group_outputs[g_key] = group.output
                        _ctx_parts.append(f"[CEO] {group.name} restarted successfully with {alt_model}.\n")
                        for i in range(group.agent_count):
                            bus.update_agent_status(f"{g_key}-{i}", {"state": "working", "model": alt_model})
                    except Exception:
                        _ctx_parts.append(f"[CEO] {group.name} still dead after model switch.\n")

            for i in range(group.agent_count):
                bus.update_agent_status(f"{g_key}-{i}", {"state": "idle"})

            if g_key == "architecture" and "sarcasm" in self.groups:
                pull = max(1, group.agent_count // 2)
                self.groups["sarcasm"].agent_count += pull
                _ctx_parts.append(f"\n[CEO] Moved {pull} agents from Architecture to Sarcasm.\n")
                bus.post(AgentMessage(sender="ceo", msg_type="negotiate",
                                      content=f"Moved {pull} architecture agents to sarcasm group."))

            _compress_ctx(f"after {g_key}")

            # Iterative fix loop after testing
            if g_key == "testing" and "worker" in self.groups:
                iteration = -1
                for iteration in range(3):
                    if not RUNNING:
                        break
                    test_output = group_outputs.get("testing", "")
                    has_failures = any(kw in test_output.lower() for kw in
                        ["fail", "error", "bug", "traceback", "assertionerror", "failed", "panic"])
                    if not has_failures:
                        break
                    _ctx_parts.append(f"\n--- Iterative Fix {iteration + 1} ---\n")
                    worker_group = self.groups["worker"]
                    fix_prompt = (
                        f"{context}\n\nWorker Group — Fix iteration {iteration + 1}. "
                        "Testing found bugs. Fix the code. Use tools to edit files. "
                        "Output ONLY the fixed code."
                    )
                    try:
                        if worker_group.model:
                            fix_output = self.llm_with_model(
                                model=worker_group.model,
                                system_prompt="Fix bugs. Use tools to edit code. Output only corrected code.",
                                user_prompt=fix_prompt,
                            )
                        else:
                            fix_output = self.llm(
                                system_prompt="Fix bugs. Use tools to edit code. Output only corrected code.",
                                user_prompt=fix_prompt,
                            )
                        worker_group.output = fix_output or ""
                        group_outputs["worker"] = worker_group.output
                        _ctx_parts.append(f"\n## Worker Fix (iter {iteration + 1})\n{worker_group.output}\n\n")
                        test_sp = system_prompts.get("testing", "")
                        test_up = f"{context}\n\nTesting Group — Retest iteration {iteration + 1}. Use tools to run tests."
                        if self.groups["testing"].model:
                            test_output = self.llm_with_model(
                                model=self.groups["testing"].model,
                                system_prompt=test_sp, user_prompt=test_up,
                            )
                        else:
                            test_output = self.llm(system_prompt=test_sp, user_prompt=test_up)
                        self.groups["testing"].output = test_output or ""
                        group_outputs["testing"] = self.groups["testing"].output
                        _ctx_parts.append(f"\n## Testing Retest (iter {iteration + 1})\n{self.groups['testing'].output}\n\n")
                    except Exception as e:
                        _ctx_parts.append(f"\n[Iterative fix {iteration + 1} failed: {e}]\n")
                        break
                _ctx_parts.append(f"\n--- Iterative fix done ({iteration + 1} rounds) ---\n\n")

        _report_status()

        context = "".join(_ctx_parts)

        # 5. CEO Final Summary
        summary_prompt = (
            "You are the CEO. Review all group outputs and write a final "
            "completion statement (3-5 sentences).\n\n"
            f"Goal: {goal}\nTotal agents: {total_agents}\n"
            f"Architecture: {(group_outputs.get('architecture') or 'N/A')[:500]}\n\n"
            f"Worker: {(group_outputs.get('worker') or 'N/A')[:500]}\n\n"
            f"Testing: {(group_outputs.get('testing') or 'N/A')[:500]}\n\n"
            f"Art: {(group_outputs.get('art') or 'N/A')[:500]}\n\n"
            f"Sarcasm: {(group_outputs.get('sarcasm') or 'N/A')[:500]}\n\n"
            f"Communication Bus: {bus.get_latest_status_snapshot()}\n\n"
            "Final completion statement:"
        )
        completion = ""
        try:
            completion = self.llm(system_prompt="", user_prompt=summary_prompt)
        except Exception:
            pass

        bus._save_checkpoint()

        self.result = {
            "goal": goal, "total_agents": total_agents,
            "models_used": model_assignments, "reasoning_effort": model_reasoning,
            "available_models": self.available_models[:10],
            "emergency_calls": bus.get_emergency_calls("approved") + bus.get_emergency_calls("pending"),
            "groups": {
                g_key: {"name": g.name, "model": g.model, "agents": g.agent_count,
                        "output": g.output[:500]}
                for g_key, g in self.groups.items()
            },
            "comm_bus_snapshot": bus.get_latest_status_snapshot(),
            "completion": completion or f"Company collaboration complete. {total_agents} agents worked on: {goal[:100]}",
            "status": "completed",
        }
        return self.result