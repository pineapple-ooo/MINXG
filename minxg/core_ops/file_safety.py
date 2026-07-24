"""minxg/core_ops/file_safety.py — Shared file-operation safety checks.

Before this module existed, AgentHarness had *two* independent implementations
of "read a file": one behind the chat-agent's function-calling registry
(``tools/file_tools.py``) and one behind the MCP worker protocol
(``minxg/workers/file/file_workers.py``). Only the first one guarded
against blocked device paths (``/dev/zero`` and friends — reading these
blocks forever or returns unbounded data), binary files, and unbounded
memory use on huge files. The MCP-exposed one had none of that.

This module is the single place those checks live now. Both call sites
import from here so a fix only has to happen once, and both surfaces
get the same protection level — that was the actual bug, not just
code duplication for its own sake.
"""
from __future__ import annotations

from pathlib import Path

#: Reading (or writing) these can block forever, return unbounded data,
#: or otherwise misbehave in ways a text-file reader shouldn't hit.
BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/tty", "/dev/console",
    "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})

#: Reject reading anything past this size as text — large binary/data
#: files should be handled with a purpose-built tool, not slurped whole
#: into an LLM's context or an MCP response payload.
MAX_READABLE_BYTES = 50 * 1024 * 1024  # 50MB


def is_blocked_path(path: str) -> bool:
    """True if `path` is one of the always-blocked device paths.

    Deliberately checks the *un-resolved* (symlink-following-free)
    normalized string first: `/dev/stdin`, `/dev/stdout`, `/dev/stderr`
    and `/dev/fd/{0,1,2}` are symlinks to something like
    `/proc/<pid>/fd/pipe:[12345]` — a different, ephemeral target on
    every single call — so `Path(...).resolve()` can never match a
    fixed path list for those. Comparing the literal path the caller
    asked for closes that gap; `.resolve()` is still checked afterwards
    to also catch e.g. a symlink someone made that points *at*
    `/dev/zero` under a different name.
    """
    try:
        expanded = Path(path).expanduser()
        normalized = str(Path(str(expanded)))  # normpath, no symlink follow
    except (OSError, RuntimeError, ValueError):
        return False
    if normalized in BLOCKED_DEVICE_PATHS:
        return True
    try:
        resolved = expanded.resolve()
    except (OSError, RuntimeError):
        return False
    if str(resolved) in BLOCKED_DEVICE_PATHS:
        return True
    return _is_special_file(expanded) or _is_special_file(resolved)


def _is_special_file(path: Path) -> bool:
    """Depth-of-defense check independent of naming: character devices,
    FIFOs and sockets aren't ordinary text files no matter what path
    they're reached through (covers aliases the fixed list doesn't
    know about, e.g. a hand-made symlink or a container-specific
    /dev layout)."""
    import stat as _stat
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return _stat.S_ISCHR(mode) or _stat.S_ISFIFO(mode) or _stat.S_ISSOCK(mode)


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    """Best-effort binary-file sniff: a NUL byte anywhere in the first
    `sample_size` bytes, or more than 30% non-printable bytes, counts as
    binary. False positives are possible (e.g. some UTF-16 text); when
    in doubt this errs toward "binary" since misreading binary as text
    is the worse failure mode for an LLM-facing file reader."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
    non_text = sum(1 for b in chunk if b not in text_chars)
    return non_text / len(chunk) > 0.3


def check_readable_text_file(path: Path) -> "tuple[bool, str]":
    """One-stop guard for "is this a safe, readable text file". Returns
    (ok, error_message) — error_message is empty when ok is True.
    Callers still need their own not-found / is-a-directory checks
    first; this only covers the safety-relevant part."""
    if is_blocked_path(str(path)):
        return False, f"Cannot read blocked device path: {path}"
    try:
        size = path.stat().st_size
    except OSError as e:
        return False, f"Cannot stat {path}: {e}"
    if size > MAX_READABLE_BYTES:
        mb = MAX_READABLE_BYTES // (1024 * 1024)
        return False, f"File too large (>{mb}MB)"
    if is_binary_file(path):
        return False, f"Binary file, not readable as text: {path}"
    return True, ""


# ── Write deny list (hermes-agent pattern) ──────────────────────────
# Exact file paths that must never be written by an AI agent tool.
_WRITE_DENIED_PATHS: set[str] = set()

# Directory prefixes that must never be written.
_WRITE_DENIED_PREFIXES: list[str] = []


def build_write_denied_paths(home: str) -> set[str]:
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "config"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            os.path.join(home, ".git-credentials"),
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            "/etc/sudoers.d",
            "/etc/systemd",
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
            os.path.join(home, ".config", "gcloud"),
        ]
    ]


def is_write_denied(path: str) -> tuple[bool, str]:
    """Return (denied, reason) for write attempts."""
    try:
        home = os.path.realpath(os.path.expanduser("~"))
        resolved = os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError) as exc:
        return True, f"path resolution failed: {exc}"

    global _WRITE_DENIED_PATHS, _WRITE_DENIED_PREFIXES
    if not _WRITE_DENIED_PATHS:
        _WRITE_DENIED_PATHS = build_write_denied_paths(home)
        _WRITE_DENIED_PREFIXES = build_write_denied_prefixes(home)

    if resolved in _WRITE_DENIED_PATHS:
        return True, f"blocked sensitive file: {resolved}"
    for prefix in _WRITE_DENIED_PREFIXES:
        if resolved.startswith(prefix):
            return True, f"blocked sensitive directory: {prefix}"

    # Block known project-local env files
    blocked_basenames = {
        ".env", ".env.local", ".env.development", ".env.production",
        ".env.test", ".env.staging", ".envrc",
    }
    if Path(resolved).name in blocked_basenames:
        return True, f"blocked env file: {resolved}"

    return False, ""
