"""AgentHarness CLI package.

Exports a single Tui class for the prompt_toolkit chat interface.
Legacy subcommands remain under multiligua_cli.main.
"""

from __future__ import annotations

from agent_harness.cli.app import Tui
from agent_harness.cli.approval import (
    PendingWrite,
    apply_pending,
    discard_pending,
    enqueue,
    get_pending,
    list_pending,
    set_write_approval,
    write_approval_enabled,
)
from agent_harness.cli.commands import SlashCommand, all_commands, get, matches, register
from agent_harness.cli.doctor import run_doctor
from agent_harness.cli.renderers import print_banner, render_tool_card, status_line

__all__ = [
    "Tui",
    "PendingWrite",
    "apply_pending",
    "discard_pending",
    "enqueue",
    "get_pending",
    "list_pending",
    "set_write_approval",
    "write_approval_enabled",
    "SlashCommand",
    "all_commands",
    "get",
    "matches",
    "register",
    "run_doctor",
    "print_banner",
    "render_tool_card",
    "status_line",
]
