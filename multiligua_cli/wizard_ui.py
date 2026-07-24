"""
AgentHarness Wizard UI Engine v3.0 — high-density native TUI.

Design targets:
  - Rich-first, ANSI fallback
  - AgentHarness color system: indigo / violet / gold / teal / emerald / slate
  - Panel / table / progress / status-bar vocabulary
  - High information density without visual noise
"""

from __future__ import annotations

import os
import re
import sys
import shutil
from typing import Any, Dict, List, Optional, Tuple

try:
    from multiligua_cli.i18n import T, set_lang, get_lang, LANGUAGES, LANG_NAMES, LANG_CODES
except ImportError:  # pragma: no cover
    def T(key, **kw): return key
    def set_lang(c): pass
    def get_lang(): return "en"
    LANGUAGES = {"en": {"flag": "EN", "name": "English", "native": "English"}}
    LANG_NAMES = ["English"]
    LANG_CODES = ["en"]

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.padding import Padding
    from rich.align import Align
    from rich.rule import Rule
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

console = Console() if HAS_RICH else None

try:
    import readchar
    HAS_READCHAR = True
except ImportError:  # pragma: no cover
    class _ReadCharKey:
        UP = "up"
        DOWN = "down"
        ENTER = "enter"
        CR = "cr"

    class _ReadCharFallback:
        key = _ReadCharKey()

        @staticmethod
        def readkey():
            raise ImportError("readchar is not installed")

    readchar = _ReadCharFallback()  # type: ignore[assignment]
    HAS_READCHAR = False


# ── AgentHarness color system ───────────────────────────────────────────────────────

class Colors:
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    ITALIC   = "\033[3m"
    UNDER    = "\033[4m"
    BLINK    = "\033[5m"
    INVERSE  = "\033[7m"

    INDIGO   = "\033[38;5;99m"
    VIOLET   = "\033[38;5;183m"
    AMETHYST = "\033[38;5;147m"
    GOLD     = "\033[38;5;220m"
    TEAL     = "\033[38;5;37m"
    SLATE    = "\033[38;5;245m"
    EMERALD  = "\033[38;5;82m"
    CORAL    = "\033[38;5;209m"
    AMBER    = "\033[38;5;214m"
    SILVER   = "\033[38;5;250m"
    SKY      = "\033[38;5;75m"
    WHITE    = "\033[38;5;255m"
    BLACK    = "\033[38;5;16m"

    BG_INDIGO   = "\033[48;5;99m"
    BG_VIOLET   = "\033[48;5;183m"
    BG_GOLD     = "\033[48;5;220m"
    BG_TEAL     = "\033[48;5;37m"
    BG_SLATE    = "\033[48;5;245m"
    BG_EMERALD  = "\033[48;5;82m"
    BG_CORAL    = "\033[48;5;209m"
    BG_AMBER    = "\033[48;5;214m"
    BG_SKY      = "\033[48;5;75m"
    BG_WHITE    = "\033[48;5;255m"
    BG_BLACK    = "\033[48;5;16m"

    @classmethod
    def f(cls, code: str) -> str:
        return f"\033[38;5;{code}m"

    @classmethod
    def bg(cls, code: str) -> str:
        return f"\033[48;5;{code}m"


# ── helpers ──────────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int = 52) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)


def _visual_width(text: str) -> int:
    try:
        import unicodedata
        width = 0
        for ch in _strip_ansi(text):
            eaw = unicodedata.east_asian_width(ch)
            width += 2 if eaw in ("F", "W") else 1
        return width
    except Exception:
        return len(_strip_ansi(text))


def _cols(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _ansi(text: str, *codes: str) -> str:
    return "".join(codes) + text + Colors.RESET


# ── primitive printers ────────────────────────────────────────────────────────

def print_banner() -> None:
    try:
        from agent_harness import VERSION
        ver = VERSION
    except Exception:
        ver = "0.18.2"

    brand = T("brand_full")
    if HAS_RICH:
        body = Text()
        body.append("AgentHarness\n", style="bold bright_white")
        body.append(f"{brand}\n", style="dim")
        body.append(f"v{ver}\n", style="italic dim cyan")
        console.print(
            Panel(
                Align.center(body),
                box=box.ROUNDED,
                border_style="bright_blue",
                padding=(1, 3),
                title="[bold bright_white]Setup[/bold bright_white]",
                subtitle="[dim]clean startup, fewer surprises[/dim]",
            )
        )
        return

    width = min(max(_cols(72), 60), 92)
    line = "╭" + "─" * (width - 2) + "╮"
    foot = "╰" + "─" * (width - 2) + "╯"
    print(_ansi(line, Colors.INDIGO))
    print(_ansi(f"│ {brand:^{width-4}} │", Colors.WHITE, Colors.BOLD))
    print(_ansi(f"│ {('v'+ver):^{width-4}} │", Colors.SLATE))
    print(_ansi(foot, Colors.INDIGO))


def print_chat_banner() -> None:
    try:
        from agent_harness import VERSION
        ver = VERSION
    except Exception:
        ver = "0.18.2"

    brand = T("brand_full")
    if HAS_RICH:
        console.print(
            Panel(
                Align.center(
                    Text.from_markup(
                        f"[bold bright_white]AgentHarness Chat[/bold bright_white]\n"
                        f"[dim]{brand}[/dim]\n"
                        f"[italic dim cyan]v{ver}[/italic dim cyan]"
                    )
                ),
                box=box.ROUNDED,
                border_style="bright_yellow",
                padding=(1, 3),
                title="[bold]Chat[/bold]",
            )
        )
        console.print(
            Panel(
                Text.from_markup(
                    "[bold bright_white]Notice[/bold bright_white]  "
                    "Local assistant UI; your outputs depend on the connected model and tools."
                ),
                box=box.SIMPLE,
                border_style="rgb(120,130,140)",
                padding=(0, 2),
            )
        )
        return

    width = min(max(_cols(72), 60), 92)
    line = "─" * width
    print(_ansi(line, Colors.SKY))
    print(_ansi(f"AgentHarness Chat — {brand}", Colors.GOLD, Colors.BOLD))
    print(_ansi(f"v{ver}", Colors.SLATE))
    print(_ansi("Local assistant UI; your outputs depend on the connected model and tools.", Colors.SLATE))
    print(_ansi(line, Colors.SKY))


# Alias for tests / backwards-compat
_wizard_chat_banner = print_chat_banner


def clear_screen() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


def print_step_progress(step: int, total: int, title: str) -> None:
    """Graphical progress header: `[2/6] ████░░ Step Name`."""
    bar_w = 32
    ratio = max(0.0, min(1.0, step / max(1, total)))
    filled = int(bar_w * ratio)
    bar_fill = "█" * filled
    bar_empty = "░" * (bar_w - filled)

    if HAS_RICH:
        header = Text()
        header.append(f"  [{step}/{total}] ", style="bold teal")
        header.append(bar_fill, style="bold gold3")
        header.append(bar_empty, style="dim")
        header.append(f"  {title}", style="bold bright_blue")
        console.print(header)
        console.print(f"  [silver]{'─' * _cols(80)}[/silver]")
        return

    header = (
        _ansi(f"[{step}/{total}]", Colors.TEAL, Colors.BOLD)
        + " "
        + _ansi(bar_fill, Colors.GOLD, Colors.BOLD)
        + _ansi(bar_empty, Colors.SLATE, Colors.DIM)
        + "  "
        + _ansi(title, Colors.BRIGHT_BLUE, Colors.BOLD)
    )
    print(header)
    print("  " + _ansi("─" * _cols(80), Colors.SLATE))


def print_section(title: str) -> None:
    if HAS_RICH:
        console.print(Rule(title, style="amethyst", characters="▸"))
        return
    print(
        "\n"
        + _ansi("▸ ", Colors.AMETHYST, Colors.BOLD)
        + _ansi(title, Colors.AMETHYST, Colors.BOLD)
    )


def print_kv(key: str, value: str, indent: int = 4) -> None:
    if HAS_RICH:
        console.print(
            f"{' ' * indent}[gold3]{key}:[/gold3] [silver]{value}[/silver]"
        )
        return
    print(
        " " * indent
        + _ansi(f"{key}:", Colors.GOLD)
        + " "
        + _ansi(value, Colors.SILVER)
    )


def print_success(msg: str) -> None:
    if HAS_RICH:
        console.print(
            f"  [bold emerald]✔[/bold emerald]  [bold emerald]{msg}[/bold emerald]"
        )
        return
    print(
        "  "
        + _ansi("✔", Colors.EMERALD, Colors.BOLD)
        + "  "
        + _ansi(msg, Colors.EMERALD)
    )


def print_error(msg: str) -> None:
    if HAS_RICH:
        console.print(
            f"  [bold coral]✖[/bold coral]  [bold coral]{msg}[/bold coral]"
        )
        return
    print(
        "  "
        + _ansi("✖", Colors.CORAL, Colors.BOLD)
        + "  "
        + _ansi(msg, Colors.CORAL)
    )


def print_warning(msg: str) -> None:
    if HAS_RICH:
        console.print(
            f"  [bold amber]⚠[/bold amber]  [bold amber]{msg}[/bold amber]"
        )
        return
    print(
        "  "
        + _ansi("⚠", Colors.AMBER, Colors.BOLD)
        + "  "
        + _ansi(msg, Colors.AMBER)
    )


def print_info(msg: str) -> None:
    if HAS_RICH:
        console.print(f"  [teal]ℹ[/teal]  [teal]{msg}[/teal]")
        return
    print(
        "  "
        + _ansi("ℹ", Colors.TEAL)
        + "  "
        + _ansi(msg, Colors.TEAL)
    )


# ── option item renderer ──────────────────────────────────────────────────────

def _render_option_rich(selected: bool, text: str, desc: str, idx: int, max_desc: int) -> str:
    marker = "▸" if selected else "▪"
    style_marker = "bold gold3" if selected else "dim"
    style_text = "bold bright_white" if selected else "bright_white"
    style_desc = "italic teal" if selected else "italic dim"
    short_desc = _truncate(desc, max_desc)
    line = f"  [{style_marker}]{marker}[/{style_marker}]  [{style_text}]{text}[/{style_text}]"
    if short_desc:
        line += f"  [{style_desc}]{short_desc}[/{style_desc}]"
    return line


def _render_option_ansi(selected: bool, text: str, desc: str, idx: int, max_desc: int) -> str:
    marker = "▸" if selected else "▪"
    short_desc = _truncate(desc, max_desc)
    if selected:
        head = _ansi(f"{marker}  {text}", Colors.GOLD, Colors.BOLD)
        tail = _ansi(f"  {short_desc}", Colors.TEAL, Colors.ITALIC) if short_desc else ""
    else:
        head = _ansi(f"{marker}  {text}", Colors.SLATE)
        tail = _ansi(f"  {short_desc}", Colors.DIM) if short_desc else ""
    return f"  {head}{tail}"


# ── AgentHarnessMenu ─────────────────────────────────────────────────────────────────

class AgentHarnessMenu:
    """Rich interactive menu with delta repaint and cached layout."""

    def __init__(
        self,
        title: str,
        options: List[str],
        descriptions: List[str] = None,
        *,
        box_style: str = "heavy_head",
        border_style: str = "bright_blue",
    ) -> None:
        self.title = title
        self.options = options
        self.descriptions = descriptions or [""] * len(options)
        self.selected = 0
        self.running = True
        self.box_style = box_style
        self.border_style = border_style

        # cached layout
        self._painted_rows = 0
        self._prev_selected = -1
        self._cols = _cols(80)
        self._max_desc_len = max(28, min(64, self._cols - 36))
        self._rich_option_lines: List[str] = []
        self._ansi_option_lines: List[str] = []
        self._rebuild_option_lines()

    # ── layout rebuild ─────────────────────────────────────────────────────

    def _rebuild_option_lines(self) -> None:
        try:
            self._cols = _cols(80)
        except Exception:
            self._cols = 80
        self._max_desc_len = max(28, min(64, self._cols - 36))
        self._rich_option_lines = [
            _render_option_rich(i == self.selected, opt, desc, i, self._max_desc_len)
            for i, (opt, desc) in enumerate(zip(self.options, self.descriptions))
        ]
        self._ansi_option_lines = [
            _render_option_ansi(i == self.selected, opt, desc, i, self._max_desc_len)
            for i, (opt, desc) in enumerate(zip(self.options, self.descriptions))
        ]

    # ── painting helpers ────────────────────────────────────────────────────

    def _paint_rich(self) -> None:
        table = Table(
            show_header=False,
            show_edge=False,
            box=box.SIMPLE,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("opt", style="", no_wrap=False, ratio=1)

        for idx, line in enumerate(self._rich_option_lines):
            selected = idx == self.selected
            if selected:
                row_style = "on rgb(20,20,35)"
            else:
                row_style = ""
            table.add_row(Text.from_markup(line), style=row_style)

        body = Panel(
            table,
            title=f"[bold gold3]{self.title}[/bold gold3]",
            title_align="left",
            border_style=self.border_style,
            box=getattr(box, self.box_style.upper(), box.HEAVY_HEAD),
            padding=(0, 1),
        )

        hint = Text(
            "  " + T("wizard_nav_hint"), style="dim"
        )
        panel = Panel(
            hint,
            border_style="rgb(120,130,140)",
            box=box.SIMPLE,
            padding=(0, 1),
        )

        console.print(body)
        console.print(panel)

    def _paint_ansi(self) -> None:
        w = self._cols
        hr = Colors.SLATE + "─" * w + Colors.RESET
        corner = Colors.INDIGO + Colors.BOLD
        top = corner + "┎" + "─" * (w - 2) + "┒" + Colors.RESET
        bottom = corner + "┖" + "─" * (w - 2) + "┚" + Colors.RESET

        print(top)
        print(
            "  "
            + _ansi(self.title, Colors.GOLD, Colors.BOLD)
            + " " * (w - _visual_width(self.title) - 4)
            + _ansi("⌖", Colors.GOLD, Colors.DIM)
        )
        print(hr)
        for line in self._ansi_option_lines:
            print(line)
        print(hr)
        print("  " + _ansi(T("wizard_nav_hint"), Colors.SLATE, Colors.DIM))
        print(bottom)

    # ── render lifecycle ────────────────────────────────────────────────────

    def _render_first(self) -> None:
        if HAS_RICH:
            self._paint_rich()
        else:
            self._paint_ansi()
        self._prev_selected = self.selected
        self._painted_rows = max(1, len(self.options) + 5)

    def _render_delta(self) -> None:
        if self._prev_selected == self.selected:
            return
        if self._painted_rows <= 0:
            self._render_first()
            return

        if HAS_RICH:
            self._rebuild_option_lines()
            self._paint_rich()
        else:
            if self._painted_rows > 1:
                sys.stdout.write(f"\033[{self._painted_rows - 1}A")
            sys.stdout.write("\033[J")
            self._paint_ansi()
        self._prev_selected = self.selected

    def _render(self) -> None:
        if self._painted_rows == 0:
            self._render_first()
            return
        self._render_delta()

    # ── public API ──────────────────────────────────────────────────────────

    def run(self) -> Optional[int]:
        if not HAS_READCHAR:
            self._render_first()
            while True:
                try:
                    print()
                    for i, opt in enumerate(self.options):
                        print(f"  {i + 1}. {opt}")
                    raw = input("  Enter choice: ").strip()
                    if raw.lower() == "q":
                        return None
                    idx = int(raw) - 1
                    if 0 <= idx < len(self.options):
                        self._move_up_and_clear()
                        self._painted_rows = 0
                        return idx
                    print_error(
                        T("err_number_range", min=1, max=len(self.options))
                    )
                except ValueError:
                    print_error(T("err_invalid_input"))
                except (KeyboardInterrupt, EOFError):
                    return None

        self._render()
        while self.running:
            key = readchar.readkey()
            if key in (readchar.key.UP, "k"):
                self.selected = (self.selected - 1) % len(self.options)
                self._render()
            elif key in (readchar.key.DOWN, "j"):
                self.selected = (self.selected + 1) % len(self.options)
                self._render()
            elif key in (readchar.key.ENTER, readchar.key.CR, "\r", "\n"):
                self._move_up_and_clear()
                self._painted_rows = 0
                return self.selected
            elif key.lower() == "q":
                self._move_up_and_clear()
                self._painted_rows = 0
                return None

    def _move_up_and_clear(self) -> None:
        if self._painted_rows <= 0:
            return
        ansi = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        if ansi:
            if self._painted_rows > 1:
                sys.stdout.write(f"\033[{self._painted_rows - 1}A")
            sys.stdout.write("\033[J")
            sys.stdout.flush()
        else:
            try:
                cols = _cols(80)
            except Exception:
                cols = 80
            clear_row = "\r" + " " * cols + "\r"
            for _ in range(self._painted_rows):
                sys.stdout.write(clear_row)
            sys.stdout.flush()


# ── simple prompt widgets ─────────────────────────────────────────────────────

def prompt(question: str, default: Optional[str] = None, password: bool = False) -> str:
    display = f"{question} [{default}]: " if default else f"{question}: "
    while True:
        try:
            if HAS_RICH:
                value = console.input(f"[gold3]{display}[/gold3]", password=password)
            else:
                value = input(_ansi(display, Colors.GOLD))
            value = value.strip()
            if value:
                return value
            if default:
                return default
            return ""
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(1)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    yes_text = T("yes")
    no_text = T("no")
    menu = AgentHarnessMenu(question, [yes_text, no_text])
    menu.selected = 0 if default else 1
    result = menu.run()
    if result is None:
        return default
    return result == 0


def prompt_choice(
    question: str,
    choices: List[str],
    descriptions: List[str] = None,
    default: int = 0,
) -> int:
    menu = AgentHarnessMenu(question, choices, descriptions)
    menu.selected = default
    result = menu.run()
    if result is None:
        return default
    return result


# ── backwards-compat shims ───────────────────────────────────────────────────

def print_option_item(
    selected: bool,
    text: str,
    desc: str = "",
    indent: int = 2,
) -> None:
    """Backwards-compat wrapper used by older tests and callers."""
    rendered = _render_option_ansi(selected, text, desc, 0, 52)
    print(" " * indent + rendered)
