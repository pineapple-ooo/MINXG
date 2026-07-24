"""AgentHarness TUI theme / color system."""

from __future__ import annotations

from typing import Any, Dict

# Minimal Hermes-inspired palette.  We stay ANSI-safe so the TUI works
# on stock Termux without TrueColor terminals.
PALETTE: Dict[str, str] = {
    "default": "default",
    "banner": "bold cyan",
    "accent": "bold magenta",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "dim": "dim",
    "tool_call": "bold blue",
    "tool_result": "cyan",
    "user": "bold white",
    "assistant": "default",
    "system": "yellow",
    "status_bg": "bg:#222222",
}


def get_style(name: str) -> str:
    return PALETTE.get(name, "default")


def build_prompt_toolkit_style() -> Dict[str, Any]:
    """Return a prompt_toolkit Style mapping for the current palette."""
    return {
        "banner": "bold cyan",
        "status": "bg:#222222 #ffffff",
        "input": "default",
        "input.multi": "default",
        "slash": "bold magenta",
        "tool-call": "bold blue",
        "tool-result": "cyan",
        "user-msg": "bold white",
        "assistant-msg": "default",
        "system-msg": "yellow",
        "error": "bold red",
        "warning": "bold yellow",
        "success": "bold green",
    }
