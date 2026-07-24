"""AgentHarness self-check (``/doctor`` slash command).

Checks:
 1. config.yaml / config.example.yaml presence + api_key redaction state
 2. worker tool registration count + key workers available
 3. URL safety module loadable
 4. write-approval modules loadable
 5. prompt_toolkit availability
 6. gateway/runtime ports reachable (best-effort)

Returns a plain text summary suitable for chat / TUI output.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _find_project_root(start: Path = Path.cwd()) -> Path:
    """Walk up until we find pyproject.toml or the agent_harness package."""
    here = start
    while True:
        if (here / "pyproject.toml").exists() or (here / "agent_harness").is_dir():
            return here
        parent = here.parent
        if parent == here:
            return start
        here = parent


def _check_config(project: Path) -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    cfg = project / "config.yaml"
    example = project / "config.example.yaml"
    ok = True
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8")
            if "«redacted»" in text or "***" in text:
                notes.append("config.yaml: api_key looks redacted ✔")
            else:
                notes.append("config.yaml: api_key NOT redacted — consider redacting")
        except Exception as exc:
            notes.append(f"config.yaml read failed: {exc}")
            ok = False
    else:
        notes.append("config.yaml: MISSING")
        ok = False
    if example.exists():
        notes.append("config.example.yaml: present ✔")
    else:
        notes.append("config.example.yaml: MISSING")
    return "config", ok, notes


def _check_workers() -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    ok = True
    try:
        from agent_harness.base import _discover_workers  # type: ignore[attr-defined]
        workers = _discover_workers()
    except Exception as exc:
        try:
            from agent_harness.server import _discover_workers  # type: ignore[attr-defined]
            workers = _discover_workers()
        except Exception as exc2:
            return "workers", False, [f"worker discovery failed: {exc2}"]
    count = len(workers) if hasattr(workers, "__len__") else "?"
    notes.append(f"workers discovered: {count}")
    if count == 0:
        ok = False
    return "workers", ok, notes


def _check_url_safety() -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    ok = True
    try:
        from agent_harness.cli.url_safety import is_safe_url, normalize_url_for_request
        assert callable(is_safe_url)
        assert callable(normalize_url_for_request)
        notes.append("url_safety: importable ✔")
    except Exception as exc:
        notes.append(f"url_safety: FAILED ({exc})")
        ok = False
    return "url_safety", ok, notes


def _check_approval() -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    ok = True
    try:
        from agent_harness.cli.approval import list_pending, enqueue, discard_pending
        assert callable(list_pending)
        assert callable(enqueue)
        assert callable(discard_pending)
        notes.append("approval: importable ✔")
    except Exception as exc:
        notes.append(f"approval: FAILED ({exc})")
        ok = False
    return "approval", ok, notes


def _check_tui() -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    ok = True
    try:
        from agent_harness.cli.app import HAS_PROMPT_TOOLKIT
        if HAS_PROMPT_TOOLKIT:
            notes.append("prompt_toolkit: available ✔")
        else:
            notes.append("prompt_toolkit: NOT available — TUI falls back to plain stdin")
    except Exception as exc:
        notes.append(f"prompt_toolkit check failed: {exc}")
        ok = False
    return "tui", ok, notes


def _check_ports() -> Tuple[str, bool, List[str]]:
    notes: List[str] = []
    ok = True
    try:
        from multiling.constants import GATEWAY_DEFAULT_PORT, WORKERS_DEFAULT_PORT
    except Exception:
        notes.append("constants: import failed — skipping port check")
        return "ports", False, notes
    for label, port in [("gateway", GATEWAY_DEFAULT_PORT), ("workers", WORKERS_DEFAULT_PORT)]:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                notes.append(f"{label} port {port}: reachable ✔")
        except (OSError, ConnectionRefusedError):
            notes.append(f"{label} port {port}: NOT listening")
    return "ports", ok, notes


def run_doctor() -> str:
    """Run all checks and return a formatted summary string."""
    project = _find_project_root()
    checks: List[Tuple[str, bool, List[str]]] = [
        _check_config(project),
        _check_workers(),
        _check_url_safety(),
        _check_approval(),
        _check_tui(),
        _check_ports(),
    ]
    lines: List[str] = ["[bold cyan]AgentHarness doctor[/bold cyan]", ""]
    overall_ok = True
    for label, ok, notes in checks:
        overall_ok &= bool(ok)
        mark = "[bold green]✔[/bold green]" if ok else "[bold red]✘[/bold red]"
        lines.append(f"{mark} {label}")
        for note in notes:
            lines.append(f"    {note}")
        lines.append("")
    status = "[bold green]ALL OK[/bold green]" if overall_ok else "[bold yellow]WARNINGS[/bold yellow]"
    lines.append(f"Result: {status}")
    return "\n".join(lines)
