"""
agent_status_ui.py — Live agent status panel for the TUI chat.

Provides Rich-powered tables and panels showing:
  - All agents (idle, working, failed, offline)
  - Current task assignments
  - Commander status (1 or 2 commanders)
  - Task board summary

Integrated into tui_chat.py via the /agents slash command.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional

from multiligua_cli.utils import console, print_dim, print_error

try:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    from rich.style import Style
    from rich.columns import Columns
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── ANSI color constants ──────────────────────────────────────────────────
_A_GREEN = "\033[32m"
_A_RED = "\033[31m"
_A_YELLOW = "\033[33m"
_A_CYAN = "\033[36m"
_A_DIM = "\033[2m"
_A_BOLD = "\033[1m"
_A_RESET = "\033[0m"
_A_BLUE = "\033[34m"
_A_GRAY = "\033[90m"

# Agent state colors
_STATE_COLORS = {
    "idle": "dim white",
    "working": "bright_cyan",
    "reporting": "bright_yellow",
    "failed": "bright_red",
    "offline": "dim gray",
}

_A_STATE_COLORS = {
    "idle": _A_GRAY,
    "working": _A_CYAN,
    "reporting": _A_YELLOW,
    "failed": _A_RED,
    "offline": _A_DIM,
}


def _state_ansi(state: str) -> str:
    return _A_STATE_COLORS.get(state, _A_RESET)


def _state_rich(state: str) -> str:
    return _STATE_COLORS.get(state, "white")


def build_agent_table(agents: List[Dict[str, Any]],
                      commander_id: str = "") -> Any:
    """Build a Rich Table showing all agents and their states.

    Returns a Rich Table (or ANSI text) that can be printed.
    """
    if not HAS_RICH:
        return _build_agent_table_ansi(agents, commander_id)

    table = Table(
        title=f"[bold bright_cyan]Agent Status — {commander_id}",
        border_style="bright_blue",
        box=box.HEAVY_HEAD,
        padding=(0, 1),
        width=80,
        title_style="bold bright_cyan",
    )
    table.add_column("ID", style="dim", width=14)
    table.add_column("Name", width=12)
    table.add_column("Role", width=10)
    table.add_column("State", width=10)
    table.add_column("Task", width=20)
    table.add_column("Idle", width=6)
    table.add_column("Done", width=4)
    table.add_column("Err", width=4)

    for a in agents:
        state = a["state"]
        state_str = f"[{_state_rich(state)}]{state}[/]"
        idle = ""
        if state == "idle" and a.get("idle_since"):
            secs = time.time() - a["idle_since"]
            if secs < 60:
                idle = f"{secs:.0f}s"
            else:
                idle = f"{secs/60:.0f}m"
        task = a.get("current_task_id") or ""
        task_short = task[:18] if task else ""
        table.add_row(
            a["id"][:12],
            a["name"],
            a["role"],
            state_str,
            task_short,
            idle,
            str(a["total_tasks_completed"]),
            str(a["total_errors"]),
        )

    return table


def _build_agent_table_ansi(agents: List[Dict[str, Any]],
                             commander_id: str) -> str:
    """ANSI fallback agent table."""
    lines = [f"  {_A_BOLD}{_A_CYAN}Agent Status — {commander_id}{_A_RESET}"]
    header = f"  {_A_DIM}{'ID':<14} {'Name':<12} {'Role':<10} {'State':<10} {'Task':<20} {'Idle':<6} Done Err{_A_RESET}"
    lines.append(header)
    lines.append(f"  {'─' * 70}")

    for a in agents:
        state = a["state"]
        sc = _state_ansi(state)
        idle = ""
        if state == "idle" and a.get("idle_since"):
            secs = time.time() - a["idle_since"]
            idle = f"{secs:.0f}s" if secs < 60 else f"{secs/60:.0f}m"
        task = a.get("current_task_id") or ""
        lines.append(
            f"  {a['id'][:12]:<14} {a['name']:<12} {a['role']:<10} "
            f"{sc}{state:<10}{_A_RESET} {task[:18]:<20} {idle:<6} "
            f"{a['total_tasks_completed']:>4} {a['total_errors']:>3}"
        )
    return "\n".join(lines)


def build_task_table(tasks: List[Dict[str, Any]],
                     commander_id: str = "") -> Any:
    """Build a Rich Table showing all tasks and their states."""
    if not HAS_RICH:
        return _build_task_table_ansi(tasks, commander_id)

    table = Table(
        title=f"[bold bright_cyan]Task Board — {commander_id}",
        border_style="deep_sky_blue3",
        box=box.HEAVY_HEAD,
        padding=(0, 1),
        width=80,
        title_style="bold bright_cyan",
    )
    table.add_column("ID", style="dim", width=10)
    table.add_column("Title", width=20)
    table.add_column("Diff", width=4)
    table.add_column("Status", width=12)
    table.add_column("Agents", width=16)
    table.add_column("Result", width=16)

    task_status_colors = {
        "pending": "dim",
        "assigned": "bright_cyan",
        "in_review": "bright_yellow",
        "completed": "bright_green",
        "failed": "bright_red",
        "blocked": "dim red",
    }

    for t in tasks:
        tc = task_status_colors.get(t["status"], "white")
        status_str = f"[{tc}]{t['status']}[/]"
        agents = ", ".join(a[:7] for a in t.get("assigned_agents", []))
        result = t.get("result", "")
        result_short = result[:14] if result else ""
        table.add_row(
            t["id"][:8],
            t["title"][:18],
            str(t["difficulty"]),
            status_str,
            agents,
            result_short,
        )

    return table


def _build_task_table_ansi(tasks: List[Dict[str, Any]],
                            commander_id: str) -> str:
    """ANSI fallback task table."""
    lines = [f"  {_A_BOLD}{_A_CYAN}Task Board — {commander_id}{_A_RESET}"]
    header = f"  {_A_DIM}{'ID':<10} {'Title':<20} {'Diff':<4} {'Status':<12} {'Agents':<16} Result{_A_RESET}"
    lines.append(header)
    lines.append(f"  {'─' * 70}")
    for t in tasks:
        agents = ", ".join(a[:7] for a in t.get("assigned_agents", []))
        result = t.get("result", "")
        lines.append(
            f"  {t['id'][:8]:<10} {t['title'][:18]:<20} {t['difficulty']:<4} "
            f"{t['status']:<12} {agents:<16} {result[:14] if result else ''}"
        )
    return "\n".join(lines)


def build_summary_panel(summary: Dict[str, Any]) -> Any:
    """Build a compact summary panel showing overall status.

    Layout:
      Commanders: N  |  Agents: M (W working, I idle, F failed)
      Tasks: T (C completed, P pending)
    """
    s = summary
    if HAS_RICH:
        text = Text()
        text.append(
            f"  Commanders: {s['num_commanders']}  |  "
            f"Agents: {s['total_agents']} "
            f"({s['working']} working, {s['idle']} idle, {s['failed']} failed)  |  "
            f"Tasks: {s['total_tasks']} "
            f"({s['completed']} completed)",
            style="bold white on rgb(16,42,105)",
        )
        return Panel(
            text,
            border_style="deep_sky_blue3",
            padding=(1, 2),
            width=80,
            title="[bold bright_cyan]Commander Status[/bold bright_cyan]",
        )
    else:
        return (
            f"\n  {_A_BOLD}{_A_CYAN}Commander Status{_A_RESET}\n"
            f"  {_A_DIM}Commanders: {s['num_commanders']}  |  "
            f"Agents: {s['total_agents']} "
            f"({s['working']} working, {s['idle']} idle, {s['failed']} failed)  |  "
            f"Tasks: {s['total_tasks']} ({s['completed']} completed){_A_RESET}\n"
        )


def _build_agent_progress_bar(agents: List[Dict[str, Any]]) -> str:
    """Build a compact progress bar showing agent state distribution."""
    if not agents:
        return ""
    total = len(agents)
    working = sum(1 for a in agents if a["state"] == "working")
    idle = sum(1 for a in agents if a["state"] == "idle")
    failed = sum(1 for a in agents if a["state"] == "failed")
    w = int(20 * working / max(total, 1))
    i = int(20 * idle / max(total, 1))
    f = max(0, 20 - w - i)
    if HAS_RICH:
        bar = (
            f"[bold bright_cyan]{'█' * w}[/]"
            f"[dim]{'░' * i}[/]"
            f"[bold red]{'▓' * f}[/]"
        )
        return f"  Agents: [{bar}]  {working} working · {idle} idle · {failed} failed"
    return f"  Agents: {working} working, {idle} idle, {failed} failed"


def show_agent_status(council) -> None:
    """Main entry: print the full agent status panel to the console."""
    try:
        status = council.get_status()
    except Exception as e:
        print_error(f"Agent status unavailable: {e}")
        return

    summary = status["summary"]

    # ── Summary header ──
    panel = build_summary_panel(summary)
    if HAS_RICH:
        console.print(panel)
    else:
        print(panel)

    # ── Per-commander agent tables ──
    cmd_indices = status.get("commanders", [])
    for i, cmd_info in enumerate(cmd_indices):
        cid = cmd_info["id"]
        cmd_agents = [a for a in status["agents"]
                      if a["commander_id"] == cid]
        cmd_tasks = [t for t in status["tasks"]
                     if t["commander_id"] == cid]

        if not cmd_agents:
            continue

        # Agent table
        agent_table = build_agent_table(cmd_agents, cid)
        if HAS_RICH:
            console.print(agent_table)
        else:
            print(agent_table)

        # Task table (compact)
        if cmd_tasks:
            task_table = build_task_table(cmd_tasks, cid)
            if HAS_RICH:
                console.print("\n")
                console.print(task_table)
            else:
                print("\n")
                print(task_table)

    # ── Breakdown note ──
    if HAS_RICH:
        console.print(Panel(
            "  Use /agents to refresh · /status for runtime info · /help for commands",
            border_style="dim blue",
            padding=(0, 2),
            width=80,
        ))
    else:
        print_dim("  Use /agents to refresh · /status for runtime info · /help for commands")