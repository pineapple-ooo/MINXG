"""AgentHarness CLI package.

Exports a single Tui class for the prompt_toolkit chat interface.
Legacy subcommands remain under multiligua_cli.main.
"""

from __future__ import annotations

from minxg.cli.app import Tui
from minxg.cli.approval import (
    PendingWrite,
    apply_pending,
    discard_pending,
    enqueue,
    get_pending,
    list_pending,
    set_write_approval,
    write_approval_enabled,
)
from minxg.cli.commands import SlashCommand, all_commands, get, matches, register
from minxg.cli.doctor import run_doctor
from minxg.cli.renderers import print_banner, render_tool_card, status_line

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
