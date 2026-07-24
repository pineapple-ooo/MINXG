"""Renderers for banner, status bar, and tool cards used by ChatTui.

These are extracted from ``multiligua_cli.tui_chat`` and adapted to the
new ``agent_harness.cli`` package so both the legacy TUI and the new
prompt_toolkit shell can share one visual language.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich import box
    from rich.markup import escape as _rich_escape
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from agent_harness.cli.theme import get_style
from multiligua_cli.themes import TOKENS

# ── Shared palette ─────────────────────────────────────────────────────
_ACCENT = TOKENS.ACCENT
_BLUE_DIM = TOKENS.BLUE_DIM
_GOLD = TOKENS.GOLD
_A_BG_DEEP = TOKENS.A_BG_DEEP
_A_BG_PANEL = TOKENS.A_BG_PANEL
_A_BLUE = TOKENS.A_BLUE
_A_CYAN = TOKENS.A_CYAN
_A_DIM_BLUE = TOKENS.A_DIM_BLUE
_A_GOLD = TOKENS.A_GOLD
_A_BOLD = TOKENS.A_BOLD
_A_DIM = TOKENS.A_DIM
_A_RESET = TOKENS.A_RESET

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# ── Helpers ───────────────────────────────────────────────────────────
def _visual_width(text: str) -> int:
    try:
        import unicodedata
        return sum(
            2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
            for ch in text
        )
    except Exception:
        return len(text)


# ── Banner ────────────────────────────────────────────────────────────
def print_banner(
    model_name: str = "",
    provider_name: str = "",
    *,
    version: str = "",
    console: Optional[Any] = None,
) -> None:
    """Render the blue-premium brand panel."""
    version = version or "0.0.0+unknown"
    if HAS_RICH:
        _console = console or _get_console()
        brand = Text()
        brand.append(f"  ◆  AgentHarness", style=f"bold {_GOLD} on rgb(12,18,40)")
        brand.append(f"\n     Multilingual Intelligence eXchange Gateway", style=f"dim white on rgb(12,18,40)")
        if model_name:
            brand.append(f"\n     ▸ Active model: ", style=f"dim white on rgb(12,18,40)")
            brand.append(model_name, style=f"bold bright_cyan on rgb(12,18,40)")
        if provider_name:
            brand.append(f"\n     ▸ Provider: ", style=f"dim white on rgb(12,18,40)")
            brand.append(provider_name, style=f"bright_cyan on rgb(12,18,40)")
        brand.append(f"\n     Version {version}", style=f"dim silver on rgb(12,18,40)")
        _console.print()
        _console.print(Panel(
            brand,
            border_style="bright_blue",
            padding=(1, 2),
            width=72,
            title=f"[bold {_GOLD}]◆[/bold {_GOLD}]",
            subtitle=f"[dim]v{version}[/dim]",
        ))
        _console.print()
    else:
        _BAR = "═" * 66
        _INDIGO = _A_BLUE
        brand_line = f"  {_A_GOLD}{_A_BOLD}◆  AgentHarness{_A_RESET}  {_A_DIM}Multilingual Intelligence eXchange Gateway{_A_RESET}"
        sys.stdout.write(f"{_INDIGO}{_A_BOLD}╔{_BAR}╗{_A_RESET}\n")
        sys.stdout.write(
            f"{_INDIGO}║{_A_BG_DEEP}{brand_line}"
            f"{' ' * max(0, 68 - _visual_width(brand_line))}"
            f"{_A_RESET}{_INDIGO}║{_A_RESET}\n"
        )
        if model_name:
            line = f"     ▸ Active model: {_A_BOLD}{_A_CYAN}{model_name}{_A_RESET}"
            sys.stdout.write(
                f"{_INDIGO}║{_A_BG_DEEP}{line}"
                f"{' ' * max(0, 68 - _visual_width(line))}"
                f"{_A_RESET}{_INDIGO}║{_A_RESET}\n"
            )
        if provider_name:
            line = f"     ▸ Provider: {_A_CYAN}{provider_name}{_A_RESET}"
            sys.stdout.write(
                f"{_INDIGO}║{_A_BG_DEEP}{line}"
                f"{' ' * max(0, 68 - _visual_width(line))}"
                f"{_A_RESET}{_INDIGO}║{_A_RESET}\n"
            )
        ver_line = f"     Version {version}"
        sys.stdout.write(
            f"{_INDIGO}║{_A_BG_DEEP}{_A_DIM}{ver_line}"
            f"{' ' * max(0, 68 - len(ver_line))}"
            f"{_A_RESET}{_INDIGO}║{_A_RESET}\n"
        )
        sys.stdout.write(f"{_INDIGO}{_A_BOLD}╚{_BAR}╝{_A_RESET}\n\n")
        sys.stdout.flush()


# ── Status bar ────────────────────────────────────────────────────────
def status_line(
    provider: str,
    model: str,
    nexus: bool = False,
    reason: str = "",
) -> str:
    """Build the status bar text (ANSI + Rich markup mix)."""
    nexus_block = f"[bold green]NEXUS[/]" if nexus else "[dim]─[/]"
    reason_block = f"[dim]reason[/] [bold]{reason or '─'}[/]" if reason else "[dim]─[/]"
    if HAS_RICH:
        return (
            f"  [dim]provider[/]  "
            f"[bold {_ACCENT}]{provider}[/]   "
            f"[dim]model[/]  [bold {_ACCENT}]{model or 'unset'}[/]   "
            f"{nexus_block}   "
            f"{reason_block}   "
            f"[dim]host {_platform_label()}[/]"
        )
    return (
        f"  {_A_DIM}provider{_A_RESET}  "
        f"{_A_BOLD}{_A_CYAN}{provider}{_A_RESET}   "
        f"{_A_DIM}model{_A_RESET}  {_A_BOLD}{_A_CYAN}{model or 'unset'}{_A_RESET}   "
        f"{_A_BOLD}{_A_CYAN if nexus else _A_DIM}{'NEXUS' if nexus else '─'}{_A_RESET}   "
        f"{_A_DIM}reason{_A_RESET}  {_A_BOLD}{_A_CYAN if reason else _A_DIM}{reason or '─'}{_A_RESET}   "
        f"{_A_DIM}host {_platform_label()}{_A_RESET}"
    )


def _platform_label() -> str:
    try:
        from multiling.platform_cap import detect_platform_key, cap_for
        return f"{detect_platform_key()} (cap {cap_for()})"
    except Exception:
        return "?"


# ── Tool card ─────────────────────────────────────────────────────────
def render_tool_card(
    name: str,
    args: dict,
    elapsed_ms: int = 0,
    warning: str = "",
    *,
    console: Optional[Any] = None,
) -> None:
    """Render a tool call as a structured card."""
    if not HAS_RICH:
        tag = f"{_A_CYAN}{_A_BOLD}⟦ {name} ⟧{_A_RESET}"
        arg_s = ""
        if args:
            try:
                import json as _j
                arg_s = f"  {_A_DIM}{_j.dumps(args, ensure_ascii=False)[:100]}{_A_RESET}"
            except Exception:
                pass
        time_s = f" {_A_DIM}({elapsed_ms}ms){_A_RESET}" if elapsed_ms else ""
        print(f"\n{tag}{arg_s}{time_s}")
        if warning:
            print(f"  {_A_DIM}{warning}{_A_RESET}")
        return
    _console = console or _get_console()
    inner = Text()
    inner.append(f"[bold cyan]{name}[/]\n")
    if args:
        try:
            import json as _j
            inner.append(f"dim {_j.dumps(args, ensure_ascii=False)[:160]}\n")
        except Exception:
            pass
    if elapsed_ms:
        inner.append(f"[dim]({elapsed_ms}ms)[/dim]\n")
    if warning:
        inner.append(f"[yellow]{warning}[/yellow]\n")
    _console.print(Panel(
        inner,
        title=f"[bold cyan]tool call[/]",
        border_style="deep_sky_blue3",
        box=box.ROUNDED,
        padding=(0, 2),
    ))


def _get_console():
    try:
        from rich.console import Console
        return Console()
    except Exception:
        return None
