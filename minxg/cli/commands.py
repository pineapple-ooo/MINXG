"""AgentHarness slash-command registry.

Single source of truth for in-TUI slash commands, their signatures,
and dispatch.  Ported from the existing ``multiligua_cli/tui_chat.py``
inline registry and expanded with Hermes-style capabilities:
  * command metadata + completion
  * confirmation gating for mutating commands
  * session export/import hooks
  * tool-call approval stubs
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


SlashHandler = Callable[[str], Any]


class SlashCommand:
    def __init__(
        self,
        name: str,
        handler: SlashHandler,
        *,
        description: str = "",
        args: str = "",
        mutating: bool = False,
        confirm: bool = False,
        category: str = "general",
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description
        self.args = args
        self.mutating = mutating
        self.confirm = confirm
        self.category = category

    @property
    def usage(self) -> str:
        if self.args:
            return f"{self.name} {self.args}".strip()
        return self.name


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, SlashCommand] = {}


def register(cmd: SlashCommand) -> None:
    _REGISTRY[cmd.name.lower()] = cmd


def get(name: str) -> Optional[SlashCommand]:
    return _REGISTRY.get(name.lower())


def all_commands() -> List[SlashCommand]:
    return list(_REGISTRY.values())


def matches(prefix: str) -> List[SlashCommand]:
    p = prefix.lower()
    return [c for c in _REGISTRY.values() if c.name.startswith(p)]


# ---------------------------------------------------------------------------
# Default commands
# ---------------------------------------------------------------------------

def _help_handler(_: str) -> str:
    lines = ["[bold cyan]Slash commands:[/bold cyan]"]
    by_cat: Dict[str, List[SlashCommand]] = {}
    for c in sorted(_REGISTRY.values(), key=lambda c: c.name):
        by_cat.setdefault(c.category, []).append(c)
    for cat, cmds in by_cat.items():
        lines.append(f"\n[dim]{cat}[/dim]")
        for c in cmds:
            arg_s = f" {c.args}" if c.args else ""
            lines.append(f"  {c.name}{arg_s}  — {c.description}")
    return "\n".join(lines)


def _clear_handler(_: str) -> str:
    return "__CLEAR__"


def _status_handler(_: str) -> str:
    return "__STATUS__"


def _doctor_handler(_: str) -> str:
    try:
        from minxg.cli.doctor import run_doctor
        return run_doctor()
    except Exception as exc:
        return f"doctor failed: {exc}"


def _tools_handler(arg: str) -> str:
    return "__TOOLS__"


def _exit_handler(_: str) -> str:
    return "__EXIT__"


register(SlashCommand("/help", _help_handler,
                     description="Show this command list", category="help"))
register(SlashCommand("/clear", _clear_handler,
                     description="Clear screen", category="layout", mutating=True))
register(SlashCommand("/status", _status_handler,
                     description="Runtime status", category="diagnostics"))
register(SlashCommand("/doctor", _doctor_handler,
                     description="Self-check (config + tools + extensions)", category="diagnostics"))
register(SlashCommand("/tools", _tools_handler,
                     description="List available tools", category="diagnostics"))
register(SlashCommand("/exit", _exit_handler,
                     description="Quit", category="system", mutating=True))
register(SlashCommand("/quit", _exit_handler,
                     description="Quit", category="system", mutating=True))
