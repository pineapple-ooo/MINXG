"""
multiligua_cli/tui_chat.py — AgentHarness TUI chat (the default ``agent_harness`` command).

A polished **blue-premium** chat surface with:

  1. **Redesigned banner** — authoritative brand panel (no block letters)
  2. **Top status bar** — provider · model (prominent) · host · depth · cost
  3. **AI typing indicator** — animated spinner while waiting for first token
  4. **Thinking indicator** — shows reasoning status (content hidden)
  5. **Slash autocomplete** — command list appears when user types ``/``
  6. **Bottom prompt** — blue prompt prefix with slash-command hints
  7. **Blue premium theme** — deep blues, cyan accents throughout

In-loop slash-command set
-------------------------
  Diagnose / inspect
    /help            Show this list
    /tools           List available tools (platform-capped)
    /status          Runtime status table
    /config          Show the active config
    /memory          Memory-tier snapshot (L0/L1/L2)
    /doctor          Self-check (config + tools + extensions)
  Memory priming
    /forget          Reset the anti-loop counter (escape a wedge)
    /reset           Reset memory engine
  Layout
    /clear           Clear screen and re-paint banner + status bar
    /history         Show last N user/assistant turns in this session
  **In-place reconfig** (no chat restart, saved immediately)
    /setup           Re-run the setup wizard with current config as defaults
    /provider [slug] Pick a provider (interactive if no arg)
    /model [name]    Switch model (interactive picker if no arg)
    /url [URL]       Set or view the API base URL
    /apikey [KEY]    Set or view the API key
    /lang [code]     Switch display language (English-only release)
  Exit
    /exit, /quit     Quit (Ctrl-D also works, empty /quit twice to be safe)

Anything that is **not** a slash command is streamed to the active model.
Each turn primes the prompt from the entropic engine so the assistant can
recall prior turns verbatim — even with cap=600 tool runs behind it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from multiligua_cli.utils import (
    console,
    ensure_config,
    get_config_path,
    load_config,
    print_dim,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from multiligua_cli.tui_input import read_line as _read_line
from multiligua_cli.themes import TOKENS
from multiligua_cli.wizard_ui import (
    HAS_READCHAR,
    HAS_RICH as _WIZARD_HAS_RICH,
    AgentHarnessMenu,
    _ansi,
    _wizard_chat_banner,
    prompt,
)

try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.console import Group
    from rich import box
    from rich.markup import escape as _rich_escape
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

# Single source of "rich available" — prefer the wizard's import (it was
# attempted first), but fall back here if the user has a partial install.
if not _WIZARD_HAS_RICH:
    HAS_RICH = False


# ═══════════════════════════════════════════════════════════════════
#  Constants & blue-premium theme
# ═══════════════════════════════════════════════════════════════════

_BRAND = "AgentHarness"
_BRAND_FULL = "Multilingual Intelligence eXchange Gateway"

# ── Palette token aliases (centralised in themes.py : TOKENS).
#    Localised names stay to keep this module's render calls unchanged.
_C_ACCENT     = TOKENS.ACCENT
_C_BLUE_DIM   = TOKENS.BLUE_DIM
_C_GOLD       = TOKENS.GOLD
# ── ANSI fallback palette (centralised; mirrors themes.TOKENS)
_A_BG_DEEP    = TOKENS.A_BG_DEEP
_A_BG_PANEL   = TOKENS.A_BG_PANEL
_A_BLUE       = TOKENS.A_BLUE
_A_CYAN       = TOKENS.A_CYAN
_A_DIM_BLUE   = TOKENS.A_DIM_BLUE
_A_GOLD       = TOKENS.A_GOLD
_A_BOLD       = TOKENS.A_BOLD
_A_DIM        = TOKENS.A_DIM
_A_RESET      = TOKENS.A_RESET

# ── Spinner frames for typing / thinking indicators
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── Slash command registry: command → description (used for autocomplete)
_SLASH_COMMANDS: Dict[str, str] = {
    "/help":     "Show this command list",
    "/tools":    "List available tools (platform-capped)",
    "/status":   "Runtime status table",
    "/agents":   "Show live agent status (Commander framework)",
    "/plan":     "Submit a goal for multi-agent collaboration",
    "/nexus":    "Toggle Nexus multi-agent mode",
    "/reason":   "Set reasoning effort: low/medium/high/xhigh",
    "/config":   "Show the active config",
    "/memory":   "Memory-tier snapshot (L0/L1/L2)",
    "/doctor":   "Self-check (config + tools + extensions)",
    "/setup":    "Re-run the setup wizard with current config",
    "/provider": "Switch AI provider — interactive picker",
    "/model":    "Switch model — interactive picker",
    "/url":      "Set or view the API base URL",
    "/apikey":   "Set or view the API key",
    "/lang":     "Switch display language",
    "/web":      "Launch browser UI (http://localhost:8080)",
    "/history":  "Show last 20 turns in this session",
    "/forget":   "Reset anti-loop counter",
    "/reset":    "Reset memory engine (cold-start)",
    "/exit":     "Quit (Ctrl-D also works)",
    "/quit":     "Quit (Ctrl-D also works)",
}


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════


def _version() -> str:
    try:
        from agent_harness import VERSION as v
        return v
    except Exception:
        return "0.0.0+unknown"


def _platform_label() -> str:
    try:
        from multiling.platform_cap import detect_platform_key, cap_for
        return f"{detect_platform_key()} (cap {cap_for()})"
    except Exception:
        return "?"


def _get_model_name(config: dict) -> str:
    """Extract the **actual** configured model name from the live config.

    Falls back to the provider's default model if the field is empty,
    so the UI always shows a real model name, never ``unset`` unless
    truly nothing is configured.
    """
    ai = config.get("ai", {})
    model = ai.get("model", "")
    if not model:
        provider = ai.get("provider", "local")
        try:
            from multiligua_cli.providers import AI_PROVIDERS
            info = AI_PROVIDERS.get(provider, {})
            model = info.get("default_model", "unset")
        except Exception:
            model = "unset"
    return model


def _get_provider_name(config: dict) -> str:
    """Get the human-readable provider name from config."""
    ai = config.get("ai", {})
    provider = ai.get("provider", "local")
    try:
        from multiligua_cli.providers import AI_PROVIDERS
        info = AI_PROVIDERS.get(provider, {})
        return info.get("name", provider)
    except Exception:
        return provider


def _status_line(provider: str, model: str, nexus: bool = False, reason: str = "") -> str:
    """Premium dashboard strip:  provider · model · host · nexus · reason · depth · cost.

    The model name is the most prominent element (bold cyan); metadata
    recedes to dimmed text so the status strip scans at a glance.
    """
    try:
        from src.ai.safety.guard import get_guard
        g = get_guard()
        depth = g.depth_guard.count
        capd = g.depth_guard.max_depth
        cost = int(g.cost_guard.total_ms)
        ceilm = int(g.cost_guard.ceiling_ms)
        depth_block = f"depth {depth}/{capd} · cost {cost:,}/{ceilm:,}ms"
    except Exception:
        depth_block = "depth ?/? · cost ?/?ms"

    nexus_block = "[bold green]NEXUS[/]" if nexus else "[dim]─[/]"
    reason_block = f"[dim]reason[/] [bold]{reason or '─'}[/]" if reason else "[dim]─[/]"
    if HAS_RICH:
        return (
            f"  [dim]provider[/]  "
            f"[bold {_C_ACCENT}]{provider}[/]   "
            f"[dim]model[/]  [bold {_C_ACCENT}]{model or 'unset'}[/]   "
            f"{nexus_block}   "
            f"{reason_block}   "
            f"[dim]host {_platform_label()}[/]   "
            f"[dim]{depth_block}[/]"
        )
    return (
        f"  {_A_DIM}provider{_A_RESET}  "
        f"{_A_BOLD}{_A_CYAN}{provider}{_A_RESET}   "
        f"{_A_DIM}model{_A_RESET}  {_A_BOLD}{_A_CYAN}{model or 'unset'}{_A_RESET}   "
        f"{_A_BOLD}{_A_CYAN if nexus else _A_DIM}{'NEXUS' if nexus else '─'}{_A_RESET}   "
        f"{_A_DIM}reason{_A_RESET}  {_A_BOLD}{_A_CYAN if reason else _A_DIM}{reason or '─'}{_A_RESET}   "
        f"{_A_DIM}host {_platform_label()}{_A_RESET}   "
        f"{_A_DIM}{depth_block}{_A_RESET}"
    )


def _save_config(config: dict) -> None:
    """Atomically save the config dict back to config.yaml."""
    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError:
        print_warning("PyYAML is missing on this Python environment.")
        return
    try:
        tmp_path = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, cfg_path)
        print_success(f"Config saved → {cfg_path}")
    except Exception as e:
        print_error(f"Save failed: {e}")
        print_info("Hint: if config.yaml is locked (Android FUSE, NFS, "
                   "root-only path) run with --write-config-env to "
                   "export the patch into AgentHarness_CONFIG instead.")


# ═══════════════════════════════════════════════════════════════════
#  Banner — redesigned, authoritative, blue-premium
# ═══════════════════════════════════════════════════════════════════


def print_banner(model_name: str = "", provider_name: str = "") -> None:
    """Render the redesigned blue-premium banner.

    Replaces the previous block-letter wordmark with a clean, authoritative
    brand panel that looks professional in any terminal.

    Layout:
      1. **Brand panel** — deep-navy background, gold ◆, white wordmark
         + full expansion, version, and the active model name in bright
         cyan so the user sees exactly what they're talking to.
      2. **Notice panel** — compact blue-background disclaimer.
    """
    version = _version()

    if HAS_RICH:
        # ── Brand panel — AgentHarness gold/indigo authoritative banner
        brand = Text()
        brand.append(f"  ◆  AgentHarness", style=f"bold gold3 on rgb(12,18,40)")
        brand.append(f"\n     {_BRAND_FULL}", style=f"dim white on rgb(12,18,40)")
        brand.append(f"\n     Enterprise AI Orchestration Engine", style=f"dim white on rgb(12,18,40)")
        brand.append(f"\n", style=f"on rgb(12,18,40)")
        if model_name:
            brand.append(f"     ▸ Active model: ", style=f"dim white on rgb(12,18,40)")
            brand.append(model_name, style=f"bold bright_cyan on rgb(12,18,40)")
            brand.append(f"\n", style=f"on rgb(12,18,40)")
        if provider_name:
            brand.append(f"     ▸ Provider: ", style=f"dim white on rgb(12,18,40)")
            brand.append(provider_name, style=f"bright_cyan on rgb(12,18,40)")
            brand.append(f"\n", style=f"on rgb(12,18,40)")
        brand.append(f"     Version {version}", style=f"dim silver on rgb(12,18,40)")

        console.print()
        console.print(Panel(
            brand,
            border_style="bright_blue",
            padding=(1, 2),
            width=72,
            title=f"[bold gold3]◆[/bold gold3]",
            subtitle=f"[dim]v{version}[/dim]",
        ))

        # ── Notice panel — compact blue disclaimer
        notice = Text()
        notice.append(
            "  NO WARRANTY · NO LEGAL ADVICE · ACTOR = USER\n"
            "  MIT-licensed, AS-IS. You are the actor, not AgentHarness.\n"
            "  Consult a qualified lawyer when the answer matters.\n",
            style="dim white on rgb(16,42,105)",
        )
        console.print(Panel(
            notice,
            border_style="deep_sky_blue3",
            padding=(0, 2),
            width=72,
        ))

        sys.stdout.write("\033[2K\r\n")
        sys.stdout.flush()
        console.print()
    else:
        # ── ANSI fallback — same shape, no rich
        _BAR = "═" * 66
        _INDIGO = TOKENS.A_BLUE   # border colour
        # Brand panel
        brand_line = f"  {_A_GOLD}{_A_BOLD}◆  AgentHarness{_A_RESET}  {_A_DIM}{_BRAND_FULL}{_A_RESET}"
        sys.stdout.write(f"{_INDIGO}{_A_BOLD}╔{_BAR}╗{_A_RESET}\n")
        sys.stdout.write(
            f"{_INDIGO}║{_A_BG_DEEP}{brand_line}"
            f"{' ' * max(0, 68 - _visual_width(brand_line))}"
            f"{_A_RESET}{_INDIGO}║{_A_RESET}\n"
        )
        sub_line = "     Enterprise AI Orchestration Engine"
        sys.stdout.write(
            f"{_INDIGO}║{_A_BG_DEEP}{_A_DIM}{sub_line}"
            f"{' ' * max(0, 68 - len(sub_line))}"
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

        # Notice panel — compact blue disclaimer
        for ln in [
            "  NO WARRANTY · NO LEGAL ADVICE · ACTOR = USER",
            "  MIT-licensed, AS-IS. You are the actor, not AgentHarness.",
            "  Consult a qualified lawyer when the answer matters.",
        ]:
            sys.stdout.write(f"{_A_BG_DEEP}{_A_DIM}{ln}{_A_RESET}\n")
        sys.stdout.write("\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════
#  Help
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
#  Helpers used by ANSI banner / fallback
# ═══════════════════════════════════════════════════════════════════

def _visual_width(text: str) -> int:
    """Approximate terminal cell width, accounting for full-width CJK."""
    try:
        import unicodedata
        return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
                   for ch in text)
    except Exception:
        return len(text)


# ═══════════════════════════════════════════════════════════════════
#  Message-role renderer — visually distinct chat roles
# ═══════════════════════════════════════════════════════════════════

def _render_user_message(text: str) -> None:
    """Render a user message with a teal accent and right-leaning surface."""
    if HAS_RICH:
        console.print(Panel(
            text,
            title=f"[{TOKENS.USER_PREFIX}]You",
            title_align="right",
            border_style="cyan",
            box=box.ROUNDED,
            style=f"white on {TOKENS.USER_BG}",
            padding=(1, 2),
        ))
    else:
        console.print(f"\n{_A_BOLD}▸ You{_A_RESET}  {text}\n")


def _render_assistant_message(text: str, model_name: str = "") -> None:
    """Render an assistant message — calm body, cyan label, version tag."""
    if HAS_RICH:
        label = f"[{TOKENS.AI_PREFIX}]{model_name or 'assistant'}"
        console.print(Panel(
            text,
            title=label,
            border_style="bright_cyan",
            box=box.ROUNDED,
            style=f"white on {TOKENS.AI_BG}",
            padding=(1, 2),
        ))
    else:
        lbl = f"{_A_BOLD}▸ {_A_CYAN}{model_name or 'assistant'}{_A_RESET}"
        console.print(f"\n{lbl}  {text}\n")


def _render_system_notice(text: str) -> None:
    """Render a system notice — looks like a banner, not a chat bubble."""
    if HAS_RICH:
        console.print(Panel(
            text,
            title=f"[{TOKENS.SYSTEM_PREFIX}]System",
            border_style="yellow",
            box=box.SIMPLE,
            padding=(0, 2),
        ))
    else:
        console.print(f"\n{_A_BOLD}ℹ System{_A_RESET}  {text}\n")


# ═══════════════════════════════════════════════════════════════════
#  Tool-call card — structured panel (replaces bare inline → name)
# ═══════════════════════════════════════════════════════════════════

def _render_tool_card(name: str, args: dict, elapsed_ms: int = 0,
                      warning: str = "") -> None:
    """Render a single tool call/result as a neat card."""
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
        console.print(f"\n{tag}{arg_s}{time_s}")
        if warning:
            console.print(f"  {_A_DIM}{warning}{_A_RESET}")
        console.print("")
        return
    # Rich card
    inner = Text()
    inner.append(f"[bold cyan]{name}[/]\n", end="")
    if args:
        try:
            import json as _j
            inner.append(
                f"{_A_DIM}{_j.dumps(args, ensure_ascii=False)[:160]}{_A_RESET}"
                if elapsed_ms else
                f"dim {_j.dumps(args, ensure_ascii=False)[:160]}"
            )
        except Exception:
            pass
    if elapsed_ms:
        inner.append(f"\n[dim]({_A_DIM}{elapsed_ms}ms){_A_RESET}")
    if warning:
        inner.append(f"\n[yellow]{warning}[/yellow]")
    console.print(Panel(
        inner if isinstance(inner, Text) else str(inner),
        title=f"[bold cyan]tool call[/]",
        border_style="deep_sky_blue3",
        box=box.ROUNDED,
        padding=(0, 2),
    ))


def show_help(active_provider: str, active_model: str) -> None:
    body = (
        f"[{_C_BLUE_DIM}]In-loop commands[/]  ·  "
        f"provider [{_C_ACCENT}]{active_provider}[/]  ·  "
        f"model [bold {_C_ACCENT}]{active_model or 'unset'}[/]\n\n"
        "[bold underline]Diagnostics[/bold underline]\n"
        "  /help            Show this list\n"
        "  /tools           List available tools (platform-capped)\n"
        "  /status          Runtime status table\n"
        "  /agents          Show live agent status (Commander framework)\n"
        "  /plan [goal]     Multi-agent collaboration — submit a goal\n"
        "  /nexus           Toggle Nexus multi-agent mode\n"
        "  /reason [level]  Set reasoning effort: low/medium/high/xhigh\n"
        "  /config          Show the active config\n"
        "  /memory          Memory-tier snapshot (L0/L1/L2)\n"
        "  /doctor          Self-check (config + tools + extensions)\n\n"
        "[bold underline]In-place reconfig (saved immediately)[/bold underline]\n"
        "  /setup                  Re-run the setup wizard using current config\n"
        "  /provider [slug]        Pick a provider — interactive picker if no arg\n"
        "  /model [name]           Switch model — interactive picker if no arg\n"
        "  /url [URL]              Set or view the API base URL\n"
        "  /apikey [KEY]           Set or view the API key (saved on enter)\n"
        "  /lang [code]            Switch display language\n\n"
        "[bold underline]Memory priming[/bold underline]\n"
        "  /history                Show the last 20 turns in this session\n"
        "  /forget                 Reset the anti-loop counter (escape a wedge)\n"
        "  /reset                  Reset memory engine\n\n"
        "[bold underline]Layout + exit[/bold underline]\n"
        "  /clear                  Clear screen, re-paint banner + status bar\n"
        "  /exit, /quit            Quit (Ctrl-D also works)"
    )
    if HAS_RICH:
        console.print(Panel(
            body,
            title=f"[bold bright_cyan]{_BRAND}[/bold bright_cyan] · help",
            border_style="deep_sky_blue3",
            box=box.HEAVY_HEAD,
            padding=(1, 2),
        ))
    else:
        print_info("Type '/help' anywhere for help.  /exit to quit.")


# ═══════════════════════════════════════════════════════════════════
#  Memory snapshot
# ═══════════════════════════════════════════════════════════════════


def show_memory_snapshot() -> None:
    """One-line summary of the entropic memory engine tiers."""
    body_lines: List[str] = []
    try:
        from src.ai.memory.entropic_evolution import get_entropic_engine
        eng = get_entropic_engine()
        st = eng.get_stats()
        body_lines.append(f"  L0 (hot):    {eng.l0.query(eng.l0._cap).__len__()} turns")
        body_lines.append(f"  L1 (warm):   {st.get('l1', {}).get('count', 0)} items "
                          f"(cap {st.get('l1', {}).get('cap', '?')})")
        body_lines.append(f"  L2 (cold):   ~ 0 bytes")
    except Exception as e:
        body_lines.append(f"  (could not read engine: {e})")
    if HAS_RICH:
        console.print(Panel(
            "\n".join(body_lines),
            title="[bold bright_cyan]Memory tiers[/bold bright_cyan]",
            border_style="deep_sky_blue3",
            box=box.HEAVY_HEAD,
            padding=(1, 2),
        ))
    else:
        print_info("memory tiers:\n" + "\n".join(body_lines))


# ═══════════════════════════════════════════════════════════════════
#  Orchestrator IO
# ═══════════════════════════════════════════════════════════════════


def _ensure_orchestrator(config: dict):
    """Build a NexusOrchestrator from the live config dict."""
    try:
        from multiling.orchestrator import NexusOrchestrator
        ai = config.get("ai", {})
        model = ai.get("model")
        if isinstance(model, str) and model.startswith("/"):
            print_warning(f"Model name '{model}' looks like a language code; resetting to default.")
            model = None
        return NexusOrchestrator(
            ai_base_url=ai.get("base_url"),
            ai_api_key=ai.get("api_key"),
            ai_provider=ai.get("provider", "local"),
            ai_model=model,
        )
    except Exception as e:
        print_error(f"Orchestrator init failed: {e}")
        return None


def _rebuild_orchestrator(config: dict):
    """Hot-reload the orchestrator when in-place commands change state."""
    return _ensure_orchestrator(config)


# ═══════════════════════════════════════════════════════════════════
#  Streaming with animated typing + thinking indicators
# ═══════════════════════════════════════════════════════════════════


async def _stream(orch, user_message: str, session_id: Optional[str],
                  history_list: List[str], model_name: str,
                  company_mode: bool = False):
    """Stream tokens with animated typing / thinking indicators.

    Behaviour:
      - Before the first token arrives, show an animated spinner:
        ``⠋ {model} is responding...`` in dim cyan.
      - When the model emits a ``thinking`` event, switch the spinner
        to ``⠋ {model} is thinking...`` in dim magenta.
        **The thinking content is NOT displayed** (no text, no tags).
      - When text tokens arrive, the spinner is replaced by the actual
        streamed text, which builds up in-place.
      - Tool calls and results are rendered inline as before.
    """
    text_parts: List[str] = []
    tool_events: List[Tuple[str, int]] = []
    idx = 0
    state = "idle"  # idle → responding → thinking → text

    # ── Background consumer: pulls events into a queue so the main
    #    loop can update the spinner animation between events.
    queue: asyncio.Queue = asyncio.Queue()

    async def _consume():
        try:
            async for event in orch.chat_stream(user_message,
                                                session_id=session_id,
                                                company_mode=company_mode):
                await queue.put(event)
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        await queue.put(None)  # sentinel — stream finished

    consumer = asyncio.create_task(_consume())

    sys.stdout.write("\n")
    sys.stdout.flush()

    if HAS_RICH:
        content = Text()  # accumulated visible content

        def _indicator(s: str, i: int) -> Text:
            sp = _SPINNER[i % len(_SPINNER)]
            if s == "thinking":
                return Text(
                    f"  {sp} {model_name} is thinking...",
                    style="dim magenta",
                )
            return Text(
                f"  {sp} {model_name} is responding...",
                style=f"dim {_C_ACCENT}",
            )

        with Live(Text(), console=console, refresh_per_second=24,
                  transient=False) as live:
            while True:
                try:
                    # Poll with a short timeout so we can animate the
                    # spinner while waiting for the next event.
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    if state in ("idle", "responding", "thinking"):
                        idx += 1
                        ind = _indicator(state, idx)
                        if content:
                            # Show content-so-far + spinner below it
                            live.update(Group(content, Text(""), ind))
                        else:
                            live.update(ind)
                    continue

                if event is None:
                    break

                kind = event.get("type")

                if kind == "text":
                    # Transition out of spinner state
                    if state in ("idle", "responding", "thinking"):
                        state = "text"
                    chunk = event.get("content", "")
                    content.append(chunk)
                    text_parts.append(chunk)
                    live.update(content)

                elif kind == "thinking":
                    # ── Show "thinking" indicator but DO NOT display
                    #    the thinking content or [thinking] tags.
                    state = "thinking"
                    # Still capture for memory engine (no visible tags)
                    text_parts.append(
                        "[thinking]" + event.get("content", "") + "[/thinking]"
                    )
                    # The next timeout tick will render the thinking
                    # spinner; no live.update needed here.

                elif kind == "tool_call":
                    if state in ("idle", "responding", "thinking"):
                        state = "text"
                    name = event.get("name", "?")
                    args = event.get("args") or {}
                    # Show tool call as a bright card
                    card = Text(f"\n⟦ {name} ⟧", style=f"bold {_C_ACCENT}")
                    if args:
                        try:
                            import json as _json
                            arg_str = _json.dumps(args, ensure_ascii=False)[:160]
                            card.append(Text(f"\n  {arg_str}", style="dim"))
                        except Exception:
                            pass
                    content.append(card)
                    tool_events.append((name, 0))
                    live.update(content)

                elif kind == "tool_result":
                    name = event.get("name", "?")
                    elapsed = int(event.get("elapsed_ms", 0))
                    # Check if result contains diff data (patch operation)
                    result = event.get("result", {})
                    elapsed_str = f"  \u2514 ({elapsed}ms)"
                    # If the tool is patch/apply_diff, render diff
                    if name in ("patch", "apply_diff", "edit_file") and isinstance(result, dict):
                        diff_str = result.get("diff", "") or result.get("output", "") or ""
                        if diff_str:
                            diff_lines = diff_str.split("\n")
                            rendered = [f"\n  \u2514 {name} \u2014 diff:"]
                            for line in diff_lines[:20]:
                                if line.startswith("+"):
                                    rendered.append(f"  \u2514 [{TOKENS.EMERALD}]{line}[/]")
                                elif line.startswith("-"):
                                    rendered.append(f"  \u2514 [{TOKENS.CORAL}]{line}[/]")
                                elif line.startswith("@@"):
                                    rendered.append(f"  \u2514 [{_C_ACCENT}]{line}[/]")
                                else:
                                    rendered.append(f"  \u2514 [dim]{line}[/]")
                            content.append(Text.from_markup("\n".join(rendered)))
                        else:
                            content.append(Text(f"\n  \u2514 {elapsed}ms", style="dim"))
                    else:
                        content.append(Text(f"\n  \u2514 {elapsed}ms", style="dim"))
                    if tool_events and tool_events[-1][0] == name:
                        tool_events[-1] = (name, elapsed)
                    if result.get("_anti_loop_warning"):
                        content.append(Text(f"\n  [yellow]{result['_anti_loop_warning']}[/]"))
                    live.update(content)

                elif kind == "error":
                    if state in ("idle", "responding"):
                        state = "text"
                    content.append(
                        f"\n[red]Error: {event.get('message')}[/red]"
                    )
                    live.update(content)

            # ── If no content was produced at all, show a dim note
            if not content:
                live.update(Text("  (no response)", style="dim"))

        await consumer
        sys.stdout.write("\n")
        sys.stdout.flush()

    else:
        # ── ANSI fallback — same logic, plain text
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                if state in ("idle", "responding", "thinking"):
                    idx += 1
                    sp = _SPINNER[idx % len(_SPINNER)]
                    if state == "thinking":
                        sys.stdout.write(
                            f"\r\033[K  {sp} {_A_DIM_BLUE}{model_name}"
                            f" is thinking...{_A_RESET}"
                        )
                    else:
                        sys.stdout.write(
                            f"\r\033[K  {sp} {_A_BLUE}{model_name}"
                            f" is responding...{_A_RESET}"
                        )
                    sys.stdout.flush()
                continue

            if event is None:
                break

            kind = event.get("type")

            if kind == "text":
                if state in ("idle", "responding", "thinking"):
                    sys.stdout.write("\r\033[K")  # clear spinner
                    state = "text"
                chunk = event.get("content", "")
                text_parts.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()

            elif kind == "thinking":
                # Indicator only — no content shown
                state = "thinking"
                text_parts.append(
                    "[thinking]" + event.get("content", "") + "[/thinking]"
                )

            elif kind == "tool_call":
                if state in ("idle", "responding", "thinking"):
                    sys.stdout.write("\r\033[K")
                    state = "text"
                name = event.get("name", "?")
                tool_events.append((name, 0))
                sys.stdout.write(f"\n{_A_CYAN}→ {name}{_A_RESET}\n")
                sys.stdout.flush()

            elif kind == "tool_result":
                name = event.get("name", "?")
                elapsed = int(event.get("elapsed_ms", 0))
                if tool_events and tool_events[-1][0] == name:
                    tool_events[-1] = (name, elapsed)
                sys.stdout.write(f"  {_A_DIM_BLUE}({elapsed}ms){_A_RESET}\n")
                sys.stdout.flush()
                res = event.get("result", {})
                if res.get("_anti_loop_warning"):
                    sys.stdout.write(
                        f"\n\033[33m{res['_anti_loop_warning']}\033[0m\n"
                    )
                    sys.stdout.flush()

            elif kind == "error":
                if state in ("idle", "responding"):
                    sys.stdout.write("\r\033[K")
                sys.stdout.write(
                    f"\n\033[31mError: {event.get('message')}\033[0m\n"
                )
                sys.stdout.flush()

        await consumer

        if not text_parts:
            sys.stdout.write("\r\033[K  (no response)\n")
            sys.stdout.flush()

        sys.stdout.write("\n")
        sys.stdout.flush()

    # ── Persist to entropic engine + session history (best-effort)
    final_text = "".join(text_parts)
    history_list.append(f"> {user_message}")
    history_list.append(
        f"AI > {final_text[:240]}{'…' if len(final_text) > 240 else ''}"
    )

    try:
        from src.ai.memory.entropic_evolution import get_entropic_engine
        eng = get_entropic_engine()
        eng.learn_from_user_message(user_message)
        if final_text:
            eng.learn_from_exchange(
                user_message, final_text,
                tool_calls=[n for n, _ in tool_events],
            )
    except Exception:
        pass

    return final_text, tool_events


# ═══════════════════════════════════════════════════════════════════
#  In-place reconfig command surface
# ═══════════════════════════════════════════════════════════════════


def _cmd_set_provider(chat_state, picker: bool = True) -> Tuple[dict, Optional[object]]:
    """Pick a provider; returns (config, new_orchestrator_or_None)."""
    from multiligua_cli.providers import AI_PROVIDERS
    config = chat_state["config"]
    cur = config.get("ai", {}).get("provider", "")
    keys = list(AI_PROVIDERS.keys())
    if picker or not cur:
        names = [f"{AI_PROVIDERS[k]['emoji']} {AI_PROVIDERS[k]['name']}"
                 for k in keys]
        descs = [AI_PROVIDERS[k]["description"] for k in keys]
        default_idx = keys.index(cur) if cur in keys else 0
        menu = AgentHarnessMenu("Select AI provider", names, descs)
        menu.selected = default_idx
        idx = menu.run()
        if idx is None:
            print_info("Provider unchanged.")
            return config, None
        provider_key = keys[idx]
    else:
        provider_key = cur

    info = AI_PROVIDERS[provider_key]
    config.setdefault("ai", {})
    config["ai"]["provider"] = provider_key
    if not config["ai"].get("base_url"):
        config["ai"]["base_url"] = info["default_url"]
    if not config["ai"].get("model"):
        config["ai"]["model"] = info["default_model"]
    _save_config(config)

    if info["needs_api_key"] and not config["ai"].get("api_key"):
        sys.stdout.write("\n")
        key = prompt("Enter API key (Enter to skip)", password=True)
        if key.strip():
            config["ai"]["api_key"] = key.strip()
            _save_config(config)

    new_orch = _rebuild_orchestrator(config)
    print_success(
        f"Provider switched → {info['name']} "
        f"({provider_key}) · model {config['ai'].get('model')}"
    )
    return config, new_orch


def _cmd_set_model(chat_state, picker: bool = True) -> Tuple[dict, Optional[object]]:
    """Pick / type a model; returns (config, new_orchestrator_or_None)."""
    config = chat_state["config"]
    ai = config.setdefault("ai", {})
    cur = ai.get("model", "")
    provider_key = ai.get("provider", "local")
    provider_info = None
    try:
        from multiligua_cli.providers import AI_PROVIDERS
        provider_info = AI_PROVIDERS.get(provider_key)
    except Exception:
        pass
    base_url = ai.get("base_url", "")
    api_key = ai.get("api_key", "")

    fetched: List[str] = []
    if base_url:
        try:
            import urllib.request, ssl, json as _json
            req = urllib.request.Request(
                base_url.rstrip("/") + "/models",
                headers={"Accept": "application/json",
                         **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
            )
            with urllib.request.urlopen(req, timeout=8,
                                        context=ssl.create_default_context()) as resp:
                data = _json.loads(resp.read().decode())
            if isinstance(data, dict):
                raw = data.get("data") or data.get("models") or []
                for m in raw:
                    n = m.get("id", m.get("name", "")) if isinstance(m, dict) else str(m)
                    if n:
                        fetched.append(n)
                fetched = fetched[:50]
        except Exception:
            pass

    candidates: List[str]
    if fetched:
        candidates = fetched
    elif provider_info:
        candidates = [provider_info["default_model"]]
    else:
        candidates = [cur] if cur else ["gpt-4o-mini"]

    if picker or (len(candidates) == 1 and candidates[0] == cur and cur):
        menu = AgentHarnessMenu(
            "Select model",
            candidates,
            [f"fetched from {provider_key or '?'} " if fetched else "default"] * len(candidates),
        )
        try:
            menu.selected = candidates.index(cur)
        except ValueError:
            menu.selected = 0
        idx = menu.run()
        if idx is None:
            print_info("Model unchanged.")
            return config, None
        chosen = candidates[idx]
    else:
        sys.stdout.write("\n")
        chosen = prompt(f"Enter model name ({cur})", cur or "")
        if not chosen.strip():
            return config, None
        chosen = chosen.strip()

    ai["model"] = chosen
    _save_config(config)
    new_orch = _rebuild_orchestrator(config)
    print_success(f"Model switched → {chosen}")
    return config, new_orch


def _cmd_set_url(chat_state, url: Optional[str] = None) -> Tuple[dict, Optional[object]]:
    config = chat_state["config"]
    ai = config.setdefault("ai", {})
    if url is None:
        sys.stdout.write("\n")
        new = prompt("API base URL", ai.get("base_url", ""))
        if not new.strip():
            return config, None
        url = new.strip()
    ai["base_url"] = url
    _save_config(config)
    new_orch = _rebuild_orchestrator(config)
    print_success(f"API URL set → {url}")
    return config, new_orch


def _cmd_set_apikey(chat_state, key: Optional[str] = None) -> Tuple[dict, Optional[object]]:
    config = chat_state["config"]
    ai = config.setdefault("ai", {})
    if key is None:
        sys.stdout.write("\n")
        new = prompt("API key", ai.get("api_key", ""), password=True)
        if not new.strip():
            return config, None
        key = new.strip()
    ai["api_key"] = key
    _save_config(config)
    new_orch = _rebuild_orchestrator(config)
    display = (key[:4] + "…" + key[-4:]) if len(key) > 8 else "set"
    print_success(f"API key updated ({display})")
    return config, new_orch


def _cmd_set_lang(code_arg: Optional[str]) -> None:
    """Switch display language (English-only release)."""
    try:
        from multiligua_cli.i18n import (
            LANGUAGES, LANG_CODES, set_lang, get_lang,
        )
        if not code_arg:
            keys = list(LANGUAGES.keys())
            names = [f"{LANGUAGES[k]['native']} ({LANGUAGES[k]['name']})"
                     for k in keys]
            descs = [f"  Code: {k}" for k in keys]
            menu = AgentHarnessMenu("Select language", names, descs)
            try:
                menu.selected = keys.index(get_lang())
            except ValueError:
                menu.selected = 0
            idx = menu.run()
            if idx is None:
                print_info("Language unchanged.")
                return
            code_arg = keys[idx]
        if code_arg not in LANGUAGES:
            print_warning(f"Unknown code: {code_arg}. "
                          f"Available: {', '.join(LANG_CODES)}")
            return
        set_lang(code_arg)
        info = LANGUAGES[code_arg]
        config = load_config()
        config["lang"] = code_arg
        _save_config(config)
        print_success(f"Language → {info['native']}")
    except Exception as e:
        print_error(f"/lang failed: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Status bar + prompt
# ═══════════════════════════════════════════════════════════════════


def _print_status_bar(provider: str, model: str, nexus: bool = False, reason: str = "") -> None:
    """Render a premium dashboard strip under the banner."""
    bar = _status_line(provider, model, nexus, reason)
    if HAS_RICH:
        console.print(Panel(
            bar,
            border_style="rgb(45,85,155)",
            box=box.SIMPLE,
            padding=(0, 1),
        ))
    else:
        sep = f"{_A_DIM_BLUE}{'─' * 64}{_A_RESET}"
        sys.stdout.write(f"\n{sep}\n{bar}\n{sep}\n\n")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════
#  Slash-command autocomplete input reader
# ═══════════════════════════════════════════════════════════════════


def _read_input(model_name: str = "") -> Optional[str]:
    """Read one user-input line with slash-command autocomplete.

    Slash autocomplete + char-by-char redraw are only safe on a real TTY.
    When stdin or stdout is a pipe / redirected / not a tty, fall back to
    the plain ``input()`` path immediately so we never block or loop-spin.
    """
    def _fallback() -> Optional[str]:
        sys.stdout.write(f"  {_A_CYAN}❯{_A_RESET} ")
        sys.stdout.flush()
        try:
            return _read_line(prompt_text="")
        except EOFError:
            return None
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return "/exit"

    # Not a TTY → skip the char-by-char path entirely.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _fallback()
    if not HAS_READCHAR:
        return _fallback()

    try:
        return _read_input_readchar()
    except Exception:
        return _fallback()


def _read_input_readchar() -> Optional[str]:
    """Character-by-character input with live slash autocomplete.

    Uses ``readchar.readkey()`` which returns full key sequences
    (including escape sequences for arrow keys).  The prompt is a
    blue ``❯`` with no model name inline (the model is already shown
    in the banner and status bar) to keep cursor arithmetic simple.

    Cursor management:
      - The input line is always line N.
      - Hint lines (if any) are lines N+1 .. N+hint_count.
      - After each redraw, the cursor is positioned at the end of
        the buffer on line N.
    """
    import readchar

    buffer = ""
    hint_count = 0  # how many hint lines are currently displayed below

    # Visible cells before the buffer: "  ❯ " = 2 spaces + ❯ + space = 4
    PROMPT_CELLS = 4

    # ── ANSI helpers for hint display
    HIDE = "\033[?25l"
    SHOW = "\033[?25h"

    def _clear_hints():
        """Erase any hint lines currently displayed below the input."""
        nonlocal hint_count
        if hint_count > 0:
            for _ in range(hint_count):
                sys.stdout.write("\033[1B\033[2K")  # down + clear line
            for _ in range(hint_count):
                sys.stdout.write("\033[1A")  # move back up
            hint_count = 0

    def _redraw():
        """Re-render the prompt + buffer + optional hint lines."""
        nonlocal hint_count

        # Clear current line, then clear any old hints below
        sys.stdout.write("\r\033[2K")
        _clear_hints()

        # Render prompt + buffer
        sys.stdout.write(f"  {_A_CYAN}❯{_A_RESET} {buffer}")

        # ── Slash autocomplete: show matching commands below the input
        if (buffer.startswith("/")
                and " " not in buffer
                and len(buffer) >= 1):
            matches = [
                (c, d) for c, d in _SLASH_COMMANDS.items()
                if c.startswith(buffer)
            ]
            if matches:
                hint_lines = []
                for cmd, desc in matches[:8]:
                    # Highlight the already-typed portion in cyan,
                    # the remainder in dim blue
                    typed_part = buffer
                    rest_part = cmd[len(buffer):]
                    desc_short = desc[:35] + "…" if len(desc) > 35 else desc
                    hint_lines.append(
                        f"  {_A_CYAN}{typed_part}{_A_RESET}"
                        f"{_A_DIM_BLUE}{rest_part}{_A_RESET}"
                        f"  {_A_DIM}{desc_short}{_A_RESET}"
                    )
                if len(matches) > 8:
                    hint_lines.append(
                        f"  {_A_DIM}… {len(matches) - 8} more{_A_RESET}"
                    )

                hint_count = len(hint_lines)
                # Write hints on lines below the input.
                # Use \r\n to ensure each hint line starts at column 0.
                sys.stdout.write("\r\n" + "\r\n".join(hint_lines))

                # Move cursor back to the input line, end of buffer
                sys.stdout.write("\r")
                for _ in range(hint_count):
                    sys.stdout.write("\033[1A")
                col = PROMPT_CELLS + len(buffer)
                if col > 0:
                    sys.stdout.write(f"\033[{col}C")

        sys.stdout.flush()

    # ── Initial draw
    sys.stdout.write(HIDE)
    _redraw()
    sys.stdout.write(SHOW)

    while True:
        key = readchar.readkey()

        # readkey() may return ""/None on some non-TTY/EOF edge cases.
        # Allow one transient empty read, then bail to fallback.
        if not key:
            raise EOFError  # caught by caller → _fallback

        # ── Enter → submit
        if key in ("\r", "\n"):
            _clear_hints()
            sys.stdout.write("\n")
            sys.stdout.flush()
            return buffer if buffer else None

        # ── Ctrl-D (EOF) → exit
        if key == "\x04":
            _clear_hints()
            sys.stdout.write("\n")
            return None

        # ── Ctrl-C → exit
        if key == "\x03":
            _clear_hints()
            sys.stdout.write("\n")
            return "/exit"

        # ── Escape → clear buffer
        if key == "\x1b":
            if buffer:
                buffer = ""
                _redraw()
            continue

        # ── Arrow keys → ignore (no history recall yet)
        if key in ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D",
                    "\x1bOA", "\x1bOB", "\x1bOC", "\x1bOD"):
            continue

        # ── Backspace / Delete
        if key in ("\x7f", "\x08"):
            if buffer:
                buffer = buffer[:-1]
                _redraw()
            continue

        # ── Tab → autocomplete
        if key == "\t":
            if buffer.startswith("/") and " " not in buffer:
                matches = [c for c in _SLASH_COMMANDS if c.startswith(buffer)]
                if len(matches) == 1:
                    buffer = matches[0] + " "
                    _redraw()
                elif len(matches) > 1:
                    # Complete the common prefix
                    common = os.path.commonprefix(matches)
                    if len(common) > len(buffer):
                        buffer = common
                        _redraw()
            continue

        # ── Regular printable character (including multi-byte UTF-8)
        if len(key) == 1 and ord(key) >= 32:
            buffer += key
            _redraw()
        elif len(key) > 1 and not key.startswith("\x1b"):
            # Multi-byte character (e.g. CJK)
            buffer += key
            _redraw()


# ═══════════════════════════════════════════════════════════════════
#  History view
# ═══════════════════════════════════════════════════════════════════


def _history_view(history: List[str]) -> None:
    """Show the most recent exchanges still in memory."""
    body_lines = history[-20:] if history else ["  (no turns yet)"]
    body = "\n".join(f"  {line}" for line in body_lines)
    if HAS_RICH:
        console.print(Panel(body, title="Session history",
                            border_style="dim blue", box=box.ROUNDED,
                            padding=(0, 2)))
    else:
        sys.stdout.write("\nSession history:\n" + body + "\n")


# ═══════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════


@ensure_config
def tui_chat(args) -> int:
    """Entry point: print banner, status bar, init orchestrator, run loop."""
    config = load_config()
    model_name = _get_model_name(config)
    provider_name = _get_provider_name(config)
    orch = _ensure_orchestrator(config)
    company_mode = False
    reasoning_effort = "medium"

    # ── Time-of-day greeting ──
    _hour = time.localtime().tm_hour
    if _hour < 6:
        _greeting = "深夜了，注意休息"
    elif _hour < 9:
        _greeting = "早上好"
    elif _hour < 12:
        _greeting = "上午好"
    elif _hour < 14:
        _greeting = "中午好"
    elif _hour < 18:
        _greeting = "下午好"
    elif _hour < 22:
        _greeting = "晚上好"
    else:
        _greeting = "夜深了，注意休息"
    print_info(_greeting)

    # ── Initial paint: banner + status bar
    print_banner(model_name=model_name, provider_name=provider_name)
    _print_status_bar(provider_name, model_name, company_mode, reasoning_effort)

    def _reload(*, provider: Optional[str] = None,
                model: Optional[str] = None) -> None:
        """Re-render status bar and rebind the orchestrator reference."""
        nonlocal config, orch, model_name, provider_name, company_mode, reasoning_effort
        config = load_config()
        if provider is not None:
            config.setdefault("ai", {})["provider"] = provider
        if model is not None:
            config.setdefault("ai", {})["model"] = model
        model_name = _get_model_name(config)
        provider_name = _get_provider_name(config)
        _print_status_bar(provider_name, model_name, company_mode, reasoning_effort)
        orch = _ensure_orchestrator(config) or orch

    history: List[str] = []
    chat_state = {"config": config}
    # ── Persistent session: same ID across launches = same memory file ──
    _session_file = Path.home() / ".agent_harness" / "session_id"
    _session_file.parent.mkdir(parents=True, exist_ok=True)
    if _session_file.exists():
        session_id: Optional[str] = _session_file.read_text(encoding="utf-8").strip() or None
    else:
        import uuid
        session_id = f"agent_harness_{uuid.uuid4().hex[:12]}"
        _session_file.write_text(session_id, encoding="utf-8")

    while True:
        # ── Read input (with slash autocomplete)
        user_input = _read_input(model_name=model_name)

        # Ctrl-D inside _read_input returns None → exit cleanly
        if user_input is None:
            sys.stdout.write("\n")
            print_success("Bye.")
            return 0

        if not user_input:
            continue

        # ── Slash-command dispatch
        cmd, _, arg = user_input.partition(" ")

        if cmd in ("/exit", "/quit", "exit", "quit"):
            print_success("Bye.")
            return 0

        if cmd == "/help":
            show_help(provider_name, model_name)
            continue

        if cmd == "/clear":
            if HAS_RICH:
                console.clear()
            else:
                os.system("cls" if os.name == "nt" else "clear")
            # Reload config to pick up any changes, then re-paint
            config = load_config()
            model_name = _get_model_name(config)
            provider_name = _get_provider_name(config)
            orch = _ensure_orchestrator(config) or orch
            print_banner(model_name=model_name, provider_name=provider_name)
            _print_status_bar(provider_name, model_name, company_mode, reasoning_effort)
            continue

        if cmd == "/history":
            _history_view(history)
            continue

        if cmd == "/setup":
            try:
                from multiligua_cli.setup import run_setup
                rc = run_setup()
                if rc == 0:
                    _reload()
                    print_success("Reconfig done. Chat continues with new settings.")
            except Exception as e:
                print_error(f"/setup failed: {e}")
            continue

        if cmd == "/provider":
            arg_key = arg.strip() or None
            non_interactive = bool(arg_key)
            try:
                new_config, new_orch = _cmd_set_provider(
                    chat_state,
                    picker=not non_interactive,
                )
                chat_state["config"] = new_config
                if new_orch is not None:
                    orch = new_orch
                _reload()
            except Exception as e:
                print_error(f"/provider failed: {e}")
            continue

        if cmd == "/model":
            arg_name = arg.strip() or None
            non_interactive = bool(arg_name)
            try:
                new_config, new_orch = _cmd_set_model(
                    chat_state,
                    picker=not non_interactive,
                )
                chat_state["config"] = new_config
                if new_orch is not None:
                    orch = new_orch
                _reload()
            except Exception as e:
                print_error(f"/model failed: {e}")
            continue

        if cmd in ("/url", "/api"):
            arg_url = arg.strip() or None
            try:
                new_config, new_orch = _cmd_set_url(chat_state, arg_url)
                chat_state["config"] = new_config
                if new_orch is not None:
                    orch = new_orch
            except Exception as e:
                print_error(f"/url failed: {e}")
            continue

        if cmd in ("/apikey", "/key"):
            arg_key = arg.strip() or None
            try:
                new_config, new_orch = _cmd_set_apikey(chat_state, arg_key)
                chat_state["config"] = new_config
                if new_orch is not None:
                    orch = new_orch
            except Exception as e:
                print_error(f"/apikey failed: {e}")
            continue

        if cmd == "/lang":
            _cmd_set_lang(arg.strip() or None)
            _reload()
            continue

        if cmd == "/tools":
            from multiligua_cli.main import run_tools
            run_tools(args)
            continue
        if cmd == "/agents":
            try:
                from multiligua_cli.agent_status_ui import show_agent_status
                from multiling.commander import CommanderCouncil
                council = getattr(orch, '_commander_council', None)
                if council is None:
                    council = CommanderCouncil()
                    council.init_commanders(num_commanders=2, agents_per=5)
                show_agent_status(council)
            except Exception as e:
                print_error(f"/agents failed: {e}")
            continue
        if cmd == "/plan":
            try:
                from multiling.commander.council import CompanyOrchestrator
                from multiligua_cli.agent_status_ui import show_agent_status
                goal = arg.strip()
                if not goal:
                    sys.stdout.write(f"  {_A_CYAN}❯{_A_RESET} Goal: ")
                    sys.stdout.flush()
                    try:
                        goal = input().strip()
                    except (EOFError, KeyboardInterrupt):
                        sys.stdout.write("\n")
                        print_info("/plan cancelled.")
                        continue
                if not goal:
                    print_info("No goal given — /plan cancelled.")
                    continue

                print_info("CEO analysing goal and estimating resources...")
                co = CompanyOrchestrator(
                    llm_complete=orch.llm_complete,
                    llm_with_model=orch.llm_complete_with_model,
                    fetch_models_fn=orch.fetch_models,
                )
                result = co.run(goal)

                print_success(
                    f"CEO decided: {result['total_agents']} agents total"
                )
                if result.get("models_used"):
                    for g_key, model in result["models_used"].items():
                        group_name = dict(CompanyOrchestrator.GROUPS).get(g_key, g_key)
                        print_dim(f"  {group_name}: {model or 'default model'} ({result['groups'][g_key]['agents']} agents)")

                # Show group outputs
                if HAS_RICH:
                    for g_key, g in result.get("groups", {}).items():
                        console.print(Panel(
                            (g.get("output") or "No output")[:400],
                            title=f"[bold cyan]{g['name']}[/] ({g['model'] or 'default'})",
                            border_style="deep_sky_blue3",
                            box=box.ROUNDED,
                            padding=(1, 2),
                        ))
                else:
                    for g_key, g in result.get("groups", {}).items():
                        print_info(f"{g['name']} ({g['model'] or 'default'}):")
                        print(f"  {(g.get('output') or 'No output')[:200]}")

                # Completion statement
                if result.get("completion"):
                    console.print(Panel(
                        result["completion"],
                        title="[bold gold3]CEO Final Statement",
                        border_style="bright_cyan",
                        box=box.ROUNDED,
                        padding=(1, 2),
                    ))

                print_success("Company collaboration complete.")
            except Exception as e:
                print_error(f"/plan failed: {e}")
            continue
        if cmd == "/nexus":
            company_mode = not company_mode
            status = "ON" if company_mode else "OFF"
            print_success(f"Nexus multi-agent mode: {status}")
            continue
        if cmd == "/reason":
            effort = arg.strip().lower()
            if effort in ("low", "medium", "high", "xhigh"):
                reasoning_effort = effort
                print_success(f"Reasoning effort set to: {effort}")
            else:
                print_info(f"Usage: /reason [low|medium|high|xhigh]  (current: {reasoning_effort})")
            continue
        if cmd == "/status":
            from multiligua_cli.main import run_status
            run_status(args)
            continue
        if cmd == "/config":
            from multiligua_cli.main import run_config_show
            run_config_show(args)
            continue
        if cmd == "/log":
            print_info("Log file lives at ~/.agent_harness/logs/ ; "
                       "use `agent_harness doctor` to inspect.")
            continue
        if cmd == "/web":
            try:
                from multiling.web_ui import launch_web_ui
                from multiling.constants import WEB_UI_DEFAULT_PORT
                port = WEB_UI_DEFAULT_PORT
                parts = arg.strip().split()
                if parts and parts[0].isdigit():
                    port = int(parts[0])
                launch_web_ui(orch, host="0.0.0.0", port=port)
            except ImportError:
                print_error("Web UI requires aiohttp. Run: pip install aiohttp")
            except Exception as e:
                print_error(f"Web UI failed: {e}")
            continue
        if cmd == "/memory":
            show_memory_snapshot()
            continue
        if cmd == "/doctor":
            from multiligua_cli.doctor import run_doctor
            rc = run_doctor(args)
            sys.stdout.write(f"\ndoctor rc={rc}\n")
            continue
        if cmd == "/forget":
            try:
                from src.ai.safety.guard import get_guard, reset_guard
                reset_guard()
                get_guard().reset()
            except Exception:
                pass
            print_success("anti-loop counter reset.")
            continue
        if cmd == "/reset":
            try:
                from src.ai.memory.entropic_evolution import reset_engine_for_tests
                reset_engine_for_tests()
            except Exception:
                pass
            print_success("memory engine reset (cold-start).")
            continue

        # ── Default → stream the user prompt to the model
        #    Clear the ❯ prompt line so the response starts clean.
        sys.stdout.write("\033[2K\r")
        sys.stdout.flush()
        try:
            try:
                from src.ai.safety.guard import get_guard
                get_guard().reset()
            except Exception:
                pass
            _render_user_message(user_input)
            t0 = time.time()
            text, tool_events = asyncio.run(
                _stream(orch, user_input, session_id=session_id,
                        history_list=history, model_name=model_name,
                        company_mode=company_mode)
            )
            elapsed = time.time() - t0
            if text and HAS_RICH:
                console.print(
                    f"  [dim]— {model_name or 'assistant'} · {elapsed:0.1f}s[/dim]"
                )
        except Exception as e:
            _render_system_notice(str(e))
            continue

        if tool_events:
            names = ", ".join(sorted({n for n, _ in tool_events}))
            console.print(f"  [dim]tools used: {names}[/dim]")
        else:
            console.print(f"  [dim]{elapsed:0.1f}s[/dim]")


if __name__ == "__main__":
    import argparse
