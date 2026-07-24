"""Slash-command handlers for the AgentHarness TUI."""

from __future__ import annotations

from typing import Any, Dict, List

from minxg.cli.theme import get_style


def handle_help() -> str:
    return "\n".join([
        f"[{get_style('accent')}]Slash commands:[/{get_style('accent')}]",
        "  /help      Show this help",
        "  /model     Show/set model",
        "  /tools     List tools",
        "  /clear     Clear screen",
        "  /status    Show status",
        "  /theme     Cycle theme",
        "  /exit      Quit",
    ])


def handle_tools(available_tools: List[Dict[str, Any]]) -> str:
    if not available_tools:
        return "No tools available."
    lines = [f"[{get_style('accent')}]Tools:[/{get_style('accent')}]"]
    for tool in available_tools[:20]:
        name = tool.get("name", "?")
        desc = tool.get("description", "")
        lines.append(f"  {name} — {desc}")
    if len(available_tools) > 20:
        lines.append(f"  ... +{len(available_tools) - 20} more")
    return "\n".join(lines)


def handle_status(state: Dict[str, Any]) -> str:
    lines = [f"[{get_style('accent')}]Status:[/{get_style('accent')}]"]
    for k, v in state.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def handle_clear() -> str:
    # prompt_toolkit handles clearing; return sentinel.
    return "__CLEAR__"
