"""AgentHarness CLI write-approval layer.

Mirrors Hermes' write-approval UX, but adapted to our codebase and
stored in ``~/.agent_harness/approvals/`` instead of Hermes' internal
paths.  Public surface is intentionally tiny:

  * ``pending(subsystem)`` — list pending writes
  * ``approve(subsystem, write_id, apply_fn)`` — apply + discard
  * ``reject(subsystem, write_id)`` — discard without apply
  * ``is_enabled(subsystem)`` / ``set_enabled(subsystem, bool)``

Subsystems we care about today:
  - ``memory``  — memory-engine writes
  - ``skills``  — skill-manager writes
  - ``config``  — config.yaml mutations from slash commands
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_AGENT_HARNESS_HOME = Path.home() / ".agent_harness"
_APPROVALS_DIR = _AGENT_HARNESS_HOME / "approvals"
_APPROVALS_DIR.mkdir(parents=True, exist_ok=True)

MEMORY = "memory"
SKILLS = "skills"
CONFIG = "config"


@dataclass
class PendingWrite:
    id: str
    subsystem: str
    summary: str
    payload: Dict[str, Any]
    origin: str = "foreground"
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))


def _state_path(subsystem: str) -> Path:
    return _APPROVALS_DIR / f"{subsystem}.json"


def _next_id(subsystem: str) -> str:
    return f"{subsystem}_{int(time.time()*1000)}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------
def _load(subsystem: str) -> Dict[str, Any]:
    path = _state_path(subsystem)
    if not path.exists():
        return {"enabled": False, "pending": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False, "pending": {}}


def _save(subsystem: str, state: Dict[str, Any]) -> None:
    tmp = _state_path(subsystem).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_state_path(subsystem))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def write_approval_enabled(subsystem: str) -> bool:
    state = _load(subsystem)
    return bool(state.get("enabled"))


def set_write_approval(subsystem: str, enabled: bool) -> None:
    state = _load(subsystem)
    state["enabled"] = bool(enabled)
    _save(subsystem, state)


def list_pending(subsystem: str) -> List[PendingWrite]:
    state = _load(subsystem)
    out: List[PendingWrite] = []
    for rec in state.get("pending", {}).values():
        try:
            out.append(PendingWrite(**rec))
        except TypeError:
            continue
    return out


def get_pending(subsystem: str, write_id: str) -> Optional[PendingWrite]:
    state = _load(subsystem)
    rec = state.get("pending", {}).get(write_id)
    if not rec:
        return None
    try:
        return PendingWrite(**rec)
    except TypeError:
        return None


def enqueue(subsystem: str, payload: Dict[str, Any], summary: str, *, origin: str = "foreground") -> PendingWrite:
    if not write_approval_enabled(subsystem):
        raise PermissionError(f"{subsystem} write-approval is disabled; cannot enqueue")
    record = PendingWrite(
        id=_next_id(subsystem),
        subsystem=subsystem,
        summary=summary,
        payload=payload,
        origin=origin,
    )
    state = _load(subsystem)
    state.setdefault("pending", {})[record.id] = {
        "id": record.id,
        "subsystem": record.subsystem,
        "summary": record.summary,
        "payload": record.payload,
        "origin": record.origin,
        "created_ms": record.created_ms,
    }
    _save(subsystem, state)
    return record


def discard_pending(subsystem: str, write_id: str) -> bool:
    state = _load(subsystem)
    pending = state.get("pending", {})
    if write_id not in pending:
        return False
    pending.pop(write_id)
    _save(subsystem, state)
    return True


def apply_pending(subsystem: str, write_id: str, apply_fn: Callable[[Dict[str, Any]], Any]) -> str:
    record = get_pending(subsystem, write_id)
    if record is None:
        return f"No pending {subsystem} write with id '{write_id}'."
    try:
        apply_fn(record.payload)
    except Exception as exc:
        return f"Apply failed: {exc}"
    discard_pending(subsystem, write_id)
    return f"Applied {subsystem} write '{write_id}'."
