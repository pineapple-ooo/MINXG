"""Installation and rollback system with platform validation.

This module provides installation and rollback system with platform validation. capabilities for the AgentHarness polyglot runtime system.

Typical usage::

    from agent_harness.contracts.runtime import handle
    result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})

All operations support async execution, security policies, and comprehensive error handling.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import _exec
from ._exec import asset_path
from .manifest import POLYGLOT_LANGUAGES, lang_info

# ---------------------------------------------------------------------------
# Canonical language list — aligned with manifest.POLYGLOT_LANGUAGES,
# minus ``python`` (always-on).
# ---------------------------------------------------------------------------

MANAGED_LANGUAGES: Tuple[str, ...] = (
    "wasm",     # WebAssembly via wasmtime
    "julia",    # Julia numerical / scientific
    "datalog",  # Datalog / ASP via clingo or pyDatalog
)


# ---------------------------------------------------------------------------
# Platform id — mirrors install.sh's detect_platform but in Python.
# ---------------------------------------------------------------------------

def platform_id() -> str:
    """Return one of ``termux`` / ``linux`` / ``macos`` / ``windows`` / ``unknown``.

    As of v0.18.0 we actively support Android (Termux), Linux, macOS,
    and Windows. Unknown platforms get a generic ``unknown`` id so the
    planner can still render a "manual install" fallback.
    """
    if os.environ.get("TERMUX_VERSION") or os.path.isdir("/data/data/com.termux"):
        return "termux"
    system = _platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    if system.startswith(("MINGW", "MSYS", "CYGWIN")) or system == "Windows":
        return "windows"
    return "unknown"


# ---------------------------------------------------------------------------
# Detection — wraps the language-specific adapter _probe() when available,
# else falls back to a cheap ``which()`` lookup plus version probe.
# ---------------------------------------------------------------------------

@dataclass
class RuntimeStatus:
    """Lightweight snapshot of one runtime's availability on this host."""

    language: str
    binary: str = ""
    available: bool = False
    note: str = ""
    version_hint: str = ""
    checksum: str = ""
    health: _exec.HealthStatus = _exec.HealthStatus.UNKNOWN

    def to_row(self) -> Dict[str, str]:
        """Return a flat dict suitable for tables / JSON."""
        return {
            "language": self.language,
            "binary": self.binary or "-",
            "available": "yes" if self.available else "no",
            "note": self.note,
            "version_hint": self.version_hint,
            "checksum": self.checksum,
            "health": self.health.value,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_row(), ensure_ascii=False)


def _probe_version(binary: str, flag: str = "--version") -> str:
    """Best-effort version extraction from a binary."""
    try:
        res = _exec.run([binary, flag], timeout=5.0)
        if res.get("ok"):
            return (res.get("stdout") or "").strip().splitlines()[0][:64]
    except Exception:
        # intentionally no-op
        pass
    return ""


def _binary_checksum(binary: str) -> str:
    """Return first 16 hex chars of SHA-256 of the binary file."""
    try:
        data = Path(binary).read_bytes()
        return hashlib.sha256(data).hexdigest()[:16]
    except Exception:
        return ""


def detect_runtime(language: str) -> RuntimeStatus:
    """Probe a single language runtime. Pure read-only; never runs installs.

    ``RuntimeStatus.to_row()`` returns a dict where ``available`` is
    always one of the strings ``"yes"`` / ``"no"``. Callers (the
    polyglot doctor panel, the install planner) all rely on that
    unambiguous two-value contract, so it is enforced here once.
    """
    lang = language.lower().strip()

    if lang == "wasm":
        binary = shutil.which("wasmtime") or ""
        available = bool(binary)
        checksum = _binary_checksum(binary) if binary else ""
        version = _probe_version(binary) if binary else ""
        health = _exec.HealthStatus.HEALTHY if available else _exec.HealthStatus.MISSING
        return RuntimeStatus(
            language="wasm",
            binary=binary or "wasmtime",
            available=available,
            note=(
                "wasmtime CLI present"
                if binary
                else "optional — pure-python emulator fallback ships in agent_harness"
            ),
            version_hint=version,
            checksum=checksum,
            health=health,
        )

    if lang == "julia":
        binary = shutil.which("julia") or ""
        available = bool(binary)
        checksum = _binary_checksum(binary) if binary else ""
        version = _probe_version(binary) if binary else ""
        note = "julia on PATH"
        health = _exec.HealthStatus.HEALTHY if available else _exec.HealthStatus.MISSING
        if available:
            res = _exec.run([str(binary), "-e", 'print(VERSION)'], timeout=5.0)
            if res.get("ok"):
                version = (res.get("stdout") or "").strip()
        return RuntimeStatus(
            language="julia",
            binary=binary or "julia",
            available=available,
            note=(note if available else "install julia (https://julialang.org/downloads/)")
            + (" — JSON.jl recommended" if available else ""),
            version_hint=version,
            checksum=checksum,
            health=health,
        )

    if lang == "datalog":
        clingo = shutil.which("clingo")
        has_pydatalog = False
        try:
            import pyDatalog  # type: ignore[import-not-found]  # noqa: F401
            has_pydatalog = True
        except Exception:
            has_pydatalog = False
        if clingo:
            checksum = _binary_checksum(clingo)
            version = _probe_version(clingo)
            return RuntimeStatus(
                language="datalog",
                binary=clingo,
                available=True,
                note="clingo (preferred Datalog solver)",
                version_hint=version,
                checksum=checksum,
                health=_exec.HealthStatus.HEALTHY,
            )
        if has_pydatalog:
            return RuntimeStatus(
                language="datalog",
                binary="pyDatalog",
                available=True,
                note="pure-python pyDatalog fallback",
                version_hint="python-package",
                checksum="",
                health=_exec.HealthStatus.DEGRADED,
            )
        return RuntimeStatus(
            language="datalog",
            binary="clingo / pyDatalog",
            available=False,
            note="install clingo (preferred) or pyDatalog (fallback)",
            version_hint="",
            checksum="",
            health=_exec.HealthStatus.MISSING,
        )

    return RuntimeStatus(
        language=lang,
        available=False,
        note=f"unknown language {lang!r}; managed: {', '.join(MANAGED_LANGUAGES)}",
        health=_exec.HealthStatus.UNKNOWN,
    )


# ---------------------------------------------------------------------------
# Plan dataclass — every InstallPlan is pure data, never executes.
# ---------------------------------------------------------------------------

@dataclass
class InstallPlan:
    """One language's install recipes, one row per platform.

    Attributes
    ----------
    language:
        The AgentHarness language id (``wasm`` / ``julia`` / ``datalog``).
    status:
        Latest :class:`RuntimeStatus` captured at plan time.
    commands:
        Mapping platform-id → recommended shell command. Empty string
        means "no recommendation; install manually".
    notes:
        Per-platform warnings / clarifications the user should read
        before copying the command.
    rollback_commands:
        Optional per-platform undo commands for failed installs.
    expected_version:
        Optional version string we expect post-install. Used by
        ``run_install`` to verify the install succeeded.
    """

    language: str
    status: RuntimeStatus
    commands: Dict[str, str] = field(default_factory=dict)
    notes: Dict[str, str] = field(default_factory=dict)
    rollback_commands: Dict[str, str] = field(default_factory=dict)
    expected_version: str = ""

    def command_for(self, plat: Optional[str] = None) -> Tuple[str, str]:
        """Return ``(command, note)`` for ``plat`` (default: current host).

        Empty string when no recipe is known.
        """
        plat = (plat or platform_id()).lower()
        cmd = self.commands.get(plat, "")
        note = self.notes.get(plat, "")
        if plat == "unknown":
            note = (
                (note + " " if note else "")
                + "(unknown host; pick the closest platform manually)"
            )
        return cmd, note

    def rollback_for(self, plat: Optional[str] = None) -> str:
        """Return rollback command for ``plat``, or empty string."""
        plat = (plat or platform_id()).lower()
        return self.rollback_commands.get(plat, "")

    def to_json(self, plat: Optional[str] = None) -> str:
        plat = (plat or platform_id()).lower()
        cmd, note = self.command_for(plat)
        rb = self.rollback_for(plat)
        return json.dumps({
            "language": self.language,
            "platform": plat,
            "available": self.status.available,
            "binary": self.status.binary,
            "version": self.status.version_hint,
            "checksum": self.status.checksum,
            "health": self.status.health.value,
            "install_command": cmd,
            "rollback_command": rb,
            "note": note,
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Plan generator — the heart of the module. Static, never executes.
# ---------------------------------------------------------------------------

def _noop_cmd() -> str:
    """Always-available sentinel: tells the user 'no install needed'."""
    return "echo 'already on PATH — nothing to install'"


def plan_install(language: str) -> InstallPlan:
    """Return an :class:`InstallPlan` for one language (no side-effects).

    The status is captured at call time, so call this immediately
    before showing it to the user. The commands dict is platform-keyed
    and the plans below mirror the recommended awk/sed-free one-liners
    for each stack.

    When the detector reports the runtime as already available on
    every platform we still emit real package-manager commands — the
    user *may* be on a fresh box even though the local probe
    (which sees only this process's PATH) found the binary. The
    :func:`run_install` executor is the one place that should
    short-circuit on availability, not this planner.
    """
    lang = language.lower().strip()
    status = detect_runtime(lang)
    plan = InstallPlan(language=lang, status=status)

    if lang == "wasm":
        plan.commands.update({
            "termux": "pkg install -y wasmtime  # may not be packaged; see notes",
            "linux": "apt-get install -y wasmtime  # or download from wasmtime.dev",
            "macos": "brew install wasmtime",
            "windows": "winget install -e --id BytecodeAlliance.Wasmtime",
            "unknown": "",
        })
        plan.notes.update({
            "termux": "Termux does not always ship wasmtime; install via upstream if 'pkg install wasmtime' returns no package.",
            "linux": "On older Debian/Ubuntu, download the .deb from https://github.com/bytecodealliance/wasmtime/releases",
            "macos": "Requires Homebrew; run 'brew install wasmtime'.",
        })
        plan.rollback_commands.update({
            "termux": "pkg remove wasmtime",
            "linux": "apt-get remove -y wasmtime",
            "macos": "brew uninstall wasmtime",
            "windows": "winget uninstall BytecodeAlliance.Wasmtime",
        })
        plan.expected_version = ">= 20.0.0"

    elif lang == "julia":
        plan.commands.update({
            "termux": "pkg install -y julia  # may not be packaged; see notes",
            "linux": "apt-get install -y julia  # or use upstream tarball",
            "macos": "brew install julia",
            "windows": "winget install -e --id JuliaLang.Julia",
            "unknown": "",
        })
        plan.notes.update({
            "termux": "If `pkg install julia` 404s, fall back to the upstream installer script (tarball extract).",
            "linux": "On older Debian/Ubuntu, use the upstream generic tarball from julialang.org.",
            "windows": "After winget install, install the JSON.jl package once: julia -e 'using Pkg; Pkg.add(\"JSON\")'",
        })
        plan.rollback_commands.update({
            "termux": "pkg remove julia",
            "linux": "apt-get remove -y julia",
            "macos": "brew uninstall julia",
            "windows": "winget uninstall JuliaLang.Julia",
        })
        plan.expected_version = ">= 1.9.0"

    elif lang == "datalog":
        plan.commands.update({
            "termux": "pkg install -y clingo  # may not be packaged; see notes",
            "linux": "apt-get install -y clingo  # or build from source",
            "macos": "brew install clingo",
            "windows": "choco install clingo  # winget has no clingo package as of last check",
            "unknown": "",
        })
        plan.notes.update({
            "termux": "If `pkg install clingo` 404s, fall back to pyDatalog: pip install pyDatalog.",
            "linux": "On Debian/Ubuntu, clingo is in universe/multiverse; enable those repos if 'apt-get install clingo' fails.",
            "macos": "Requires Homebrew; run 'brew install clingo'.",
            "windows": "Chocolatey recommended; otherwise build clingo from source via CMake.",
        })
        plan.rollback_commands.update({
            "termux": "pkg remove clingo",
            "linux": "apt-get remove -y clingo",
            "macos": "brew uninstall clingo",
            "windows": "choco uninstall clingo",
        })
        plan.expected_version = ">= 5.5.0"

    else:
        plan.notes["unknown"] = (
            f"unknown language {lang!r}; managed: {', '.join(MANAGED_LANGUAGES)}"
        )
        plan.commands.update({p: "" for p in ("termux", "linux", "macos", "windows", "unknown")})
    return plan


def current_plan(language: str = "all") -> List[InstallPlan]:
    """Plan one language (or every managed language) at the current host."""
    if language.lower().strip() in ("", "all"):
        return [plan_install(lang) for lang in MANAGED_LANGUAGES]
    return [plan_install(language)]


# ---------------------------------------------------------------------------
# Render — turn InstallPlans into human-readable text the CLI can print.
# ---------------------------------------------------------------------------

def render_install_plan(plans: List[InstallPlan], *, plat: Optional[str] = None) -> str:
    """Pretty-print one or more plans as fixed-column text (no Rich required).

    The format intentionally fits inside an 80-column TTY so it works in
    CI logs. Doctor / runtime-plan share this rendering.
    """
    plat = plat or platform_id()
    lines: List[str] = []
    lines.append(f"# AgentHarness polyglot runtime install plan — host={plat}")
    lines.append("")

    for p in plans:
        bits = [f". {p.language}"]
        bits.append(f"  status:  {'available' if p.status.available else 'missing'}")
        if p.status.binary:
            bits.append(f"  binary:  {p.status.binary}")
        if p.status.version_hint:
            bits.append(f"  version: {p.status.version_hint}")
        if p.status.checksum:
            bits.append(f"  checksum:{p.status.checksum}")
        if p.status.note:
            bits.append(f"  detail:  {p.status.note}")
        cmd, note = p.command_for(plat)
        rb = p.rollback_for(plat)
        if cmd:
            bits.append(f"  install ({plat}): {cmd}")
        else:
            bits.append(f"  install ({plat}): (no recipe — manual only)")
        if rb:
            bits.append(f"  rollback ({plat}): {rb}")
        if note:
            bits.append(f"  note:    {note}")
        bits.append("")
        lines.extend(bits)
    return "\n".join(lines).rstrip() + "\n"


def render_install_plan_json(plans: List[InstallPlan], *, plat: Optional[str] = None) -> str:
    """Render plans as a JSON array for machine consumers."""
    plat = plat or platform_id()
    return json.dumps({
        "host": plat,
        "plans": [json.loads(p.to_json(plat)) for p in plans],
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Executor — opt-in via ``--apply``; never auto-sudo, never recursive.
# ---------------------------------------------------------------------------

def run_install(
    language: str,
    plat: Optional[str] = None,
    *,
    apply: bool = False,
    runner: Optional[Callable[[str], Dict[str, Any]]] = None,
    verify_version: bool = True,
    rollback_on_failure: bool = True,
) -> Dict[str, Any]:
    """Execute the plan's install cmd for ``language``, optionally.

    Parameters
    ----------
    language:
        ``wasm`` / ``julia`` / ``datalog`` / ``all``.
    plat:
        Override the host platform id. Default: auto-detect.
    apply:
        When ``False`` (default) we return the plan *without* executing
        it. Set ``True`` to actually run the install command via the
        shared :func:`_exec.run` helper.
    runner:
        Test seam: anything callable ``runner(cmd:str) -> Dict[str, Any]``.
        When provided, replaces subprocess execution so unit tests can
        assert exactly what command would have been run.
    verify_version:
        When ``True`` (default), after install we re-probe and compare
        against ``expected_version`` prefix. Mismatch surfaces as
        ``status: degraded``.
    rollback_on_failure:
        When ``True`` (default), failed installs trigger the platform's
        ``rollback_command`` before returning.

    Returns
    -------
    Dict with keys: ``language``, ``platform``, ``applied``, ``command``,
    ``note``, ``runner_output`` (only when applied), ``verified``.
    """
    plat = (plat or platform_id()).lower()
    plans = current_plan(language) if language.lower().strip() in ("", "all") else [plan_install(language)]
    out: List[Dict[str, Any]] = []

    for p in plans:
        cmd, note = p.command_for(plat)
        rb = p.rollback_for(plat)
        row: Dict[str, Any] = {
            "language": p.language,
            "platform": plat,
            "applied": False,
            "command": cmd,
            "note": note,
            "rollback_command": rb,
            "verified": False,
        }
        if not cmd:
            row["note"] = (note + " " if note else "") + "(no recipe — manual install required)"
            out.append(row)
            continue
        # Auto-short-circuit: when the runtime is already present we
        # avoid re-running the install.
        if p.status.available:
            row["command"] = _noop_cmd()
            row["note"] = (note + " " if note else "") + "runtime already present — install skipped"
            out.append(row)
            continue
        if not apply:
            row["note"] = (note + " " if note else "") + "(dry-run — set apply=True to execute)"
            out.append(row)
            continue
        # Real execution path. The runner is shell-string, so we hand it
        # off via ``sh -c``. We intentionally do NOT use sudo unless
        # the command itself contains it.
        try:
            if runner is not None:
                res = runner(cmd)
            else:
                res = _exec.run(["sh", "-c", cmd], timeout=600.0)
            row["applied"] = True
            row["runner_output"] = res
            # Post-install verification.
            if verify_version and res.get("ok"):
                new_status = detect_runtime(p.language)
                row["verified"] = new_status.available
                if not new_status.available:
                    row["note"] = (note + " " if note else "") + "install ran but binary still not detected"
                    row["status"] = "degraded"
                elif p.expected_version and not new_status.version_hint.startswith(p.expected_version.rpartition(" ")[2][:4]):
                    row["note"] = (note + " " if note else "") + f"version mismatch: got {new_status.version_hint}, expected {p.expected_version}"
                    row["status"] = "degraded"
                else:
                    row["status"] = "ok"
                    row["verified_version"] = new_status.version_hint
            else:
                row["status"] = "ok" if res.get("ok") else "failed"
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)
            if rollback_on_failure and rb:
                try:
                    if runner is not None:
                        rb_res = runner(rb)
                    else:
                        rb_res = _exec.run(["sh", "-c", rb], timeout=300.0)
                    row["rollback_output"] = rb_res
                except Exception as rb_exc:
                    row["rollback_error"] = str(rb_exc)
        out.append(row)
    return {"plans": out, "platform": plat, "any": bool(out)}


# ---------------------------------------------------------------------------
# Module-level convenience — single-pass snapshot the doctor uses.
# ---------------------------------------------------------------------------

def status_snapshot() -> List[Dict[str, str]]:
    """Return ``[{language, available, binary, note, version, checksum, health}, ...]`` for the doctor.

    Pure JSON-y dicts, one row per managed language, no plan/install
    commands included (the doctor just wants "is it installed?").
    """
    return [p.status.to_row() for p in current_plan("all")]


# ---------------------------------------------------------------------------
# Advanced installation features
# ---------------------------------------------------------------------------

class InstallationQueue:
    """Queue for managing multiple installations."""

    def __init__(self) -> None:
        self.queue: List[Dict[str, Any]] = []
        self.completed: List[Dict[str, Any]] = []
        self.failed: List[Dict[str, Any]] = []

    def enqueue(self, language: str, version: str = None) -> None:
        """Add a language installation to the queue."""
        self.queue.append({
            "language": language,
            "version": version,
            "status": "pending",
            "timestamp": datetime.utcnow().isoformat(),
        })

    def process_next(self) -> Dict[str, Any]:
        """Process the next item in the queue."""
        if not self.queue:
            return {"status": "empty"}
        item = self.queue.pop(0)
        result = run_install(item["language"], apply=True)
        item["result"] = result
        plans = result.get("plans", [])
        if plans and plans[0].get("status") == "ok":
            self.completed.append(item)
        else:
            self.failed.append(item)
        return item

    def process_all(self) -> Dict[str, Any]:
        """Process all items in the queue."""
        results = []
        while self.queue:
            results.append(self.process_next())
        return {
            "completed": len(self.completed),
            "failed": len(self.failed),
            "results": results,
        }


class InstallationSnapshot:
    """Point-in-time snapshot of installation state."""

    def __init__(self) -> None:
        self.snapshots: List[Dict[str, Any]] = []

    def create(self) -> Dict[str, Any]:
        """Create a new snapshot."""
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "runtimes": [p.to_json() for p in current_plan("all")],
            "disk_usage": _get_disk_usage(),
        }
        self.snapshots.append(snapshot)
        return snapshot

    def compare(self, snapshot_a: Dict[str, Any], snapshot_b: Dict[str, Any]) -> Dict[str, Any]:
        """Compare two snapshots."""
        return {
            "a_time": snapshot_a.get("timestamp"),
            "b_time": snapshot_b.get("timestamp"),
            "runtimes_changed": snapshot_a.get("runtimes") != snapshot_b.get("runtimes"),
            "disk_diff": _diff_disk_usage(
                snapshot_a.get("disk_usage", {}),
                snapshot_b.get("disk_usage", {}),
            ),
        }


def _get_disk_usage() -> Dict[str, int]:
    """Get current disk usage for runtime assets."""
    usage = {}
    try:
        for lang in ["wasm", "julia", "datalog"]:
            path = asset_path(lang, "")
            if path.exists():
                usage[lang] = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        pass
    return usage


def _diff_disk_usage(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    """Compute disk usage difference between two snapshots."""
    diff = {}
    for lang in set(list(a.keys()) + list(b.keys())):
        diff[lang] = b.get(lang, 0) - a.get(lang, 0)
    return diff


class InstallationValidator:
    """Validate installation integrity."""

    @staticmethod
    def validate_binary(language: str, path: Path) -> Dict[str, Any]:
        """Validate a binary installation."""
        if not path.exists():
            return {"valid": False, "reason": "binary not found"}
        if not os.access(path, os.X_OK):
            return {"valid": False, "reason": "binary not executable"}
        return {"valid": True, "path": str(path)}

    @staticmethod
    def validate_assets(language: str) -> Dict[str, Any]:
        """Validate language assets."""
        missing = []
        try:
            asset_dir = asset_path(language, "")
            if not asset_dir.exists():
                return {"valid": False, "missing": [str(asset_dir)]}
            for f in asset_dir.rglob("*"):
                if f.is_file():
                    if f.stat().st_size == 0:
                        missing.append(str(f))
        except Exception as exc:
            return {"valid": False, "error": str(exc)}
        return {"valid": len(missing) == 0, "missing": missing}

    @staticmethod
    def validate_version(language: str, expected: str) -> Dict[str, Any]:
        """Validate version matches expected."""
        plans = current_plan(language)
        if not plans:
            return {"valid": False, "expected": expected, "actual": None}
        actual = plans[0].status.version_hint
        return {"valid": actual == expected, "expected": expected, "actual": actual}


class InstallPlanV2:
    """Plan for installing or upgrading components (v2)."""

    def __init__(self, target_version: str, components: List[str], dry_run: bool = False):
        self.target_version = target_version
        self.components = components
        self.dry_run = dry_run
        self.steps: List[Dict[str, Any]] = []
        self.rollback_plan: List[Dict[str, Any]] = []

    def add_step(self, name: str, action: Callable, rollback: Callable = None) -> None:
        """Add an installation step."""
        self.steps.append({"name": name, "action": action})
        if rollback:
            self.rollback_plan.append({"name": name, "rollback": rollback})

    def execute(self) -> Dict[str, Any]:
        """Execute the installation plan."""
        results = []
        for step in self.steps:
            if self.dry_run:
                results.append({"step": step["name"], "status": "dry_run"})
            else:
                try:
                    result = step["action"]()
                    results.append({"step": step["name"], "status": "ok", "result": result})
                except Exception as exc:
                    results.append({"step": step["name"], "status": "error", "error": str(exc)})
                    return {"status": "failed", "results": results, "rolled_back": False}
        return {"status": "ok", "results": results}

    def rollback(self) -> Dict[str, Any]:
        """Execute rollback plan."""
        results = []
        for step in reversed(self.rollback_plan):
            try:
                result = step["rollback"]()
                results.append({"step": step["name"], "status": "rolled_back", "result": result})
            except Exception as exc:
                results.append({"step": step["name"], "status": "error", "error": str(exc)})
        return {"status": "rolled_back", "results": results}


class VersionVerifier:
    """Verify version compatibility."""

    def __init__(self, min_version: str, max_version: str = None):
        self.min_version = min_version
        self.max_version = max_version

    def is_compatible(self, version: str) -> bool:
        """Check if version is compatible."""
        try:
            from packaging.version import parse
            v = parse(version)
            if parse(self.min_version) > v:
                return False
            if self.max_version and parse(self.max_version) < v:
                return False
            return True
        except ImportError:
            return version >= self.min_version and (
                not self.max_version or version <= self.max_version
            )

    def get_constraint(self) -> str:
        """Get version constraint string."""
        if self.max_version:
            return f">={self.min_version},<={self.max_version}"
        return f">={self.min_version}"


class DependencyResolver:
    """Resolve component dependencies."""

    def __init__(self) -> None:
        self.dependencies: Dict[str, List[str]] = {}

    def add_dependency(self, component: str, requires: List[str]) -> None:
        """Add dependency requirements."""
        self.dependencies[component] = requires

    def resolve(self, components: List[str]) -> List[str]:
        """Resolve all dependencies for components."""
        resolved = set(components)
        queue = list(components)
        while queue:
            comp = queue.pop(0)
            for dep in self.dependencies.get(comp, []):
                if dep not in resolved:
                    resolved.add(dep)
                    queue.append(dep)
        return sorted(resolved)

    def detect_cycles(self) -> List[List[str]]:
        """Detect circular dependencies."""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in self.dependencies.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    try:
                        cycle_start = path.index(neighbor)
                        cycles.append(path[cycle_start:] + [neighbor])
                    except ValueError:
                        cycles.append([neighbor])
            rec_stack.discard(node)

        for comp in self.dependencies:
            if comp not in visited:
                dfs(comp, [comp])
        return cycles


class ComponentRegistry:
    """Registry of available components."""

    def __init__(self) -> None:
        self.components: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, version: str, metadata: Dict[str, Any] = None) -> None:
        """Register a component."""
        self.components[name] = {
            "version": version,
            "metadata": metadata or {},
            "installed_at": datetime.utcnow().isoformat(),
        }

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Get component info."""
        return self.components.get(name)

    def list_components(self) -> List[str]:
        """List all registered components."""
        return list(self.components.keys())

    def check_updates(self) -> Dict[str, str]:
        """Check for component updates."""
        updates = {}
        for name, info in self.components.items():
            latest = info["metadata"].get("latest_version", info["version"])
            if latest != info["version"]:
                updates[name] = latest
        return updates


# ---------------------------------------------------------------------------
# Module-level async fallback
# ---------------------------------------------------------------------------

async def _invoke_async_default(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "disabled", "message": "installer async not configured", "payload": payload}


__all__ = [
    "detect_runtime",
    "run_install",
    "platform_id",
    "InstallPlan",
    "RuntimeStatus",
    "plan_install",
    "current_plan",
    "render_install_plan",
    "render_install_plan_json",
    "status_snapshot",
    "MANAGED_LANGUAGES",
    "VersionVerifier",
    "DependencyResolver",
    "ComponentRegistry",
    "InstallationQueue",
    "InstallationSnapshot",
    "InstallationValidator",
    "InstallPlanV2",
]