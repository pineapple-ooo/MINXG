"""AgentHarness Runtime Refactor Timer — tracks elapsed time for the 1400-step plan."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).with_name("_refactor_timer_state.json")

def start():
    state = {
        "started_at": datetime.utcnow().isoformat(),
        "step": 0,
        "phase": "init",
        "milestones": [],
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[timer] started at {state['started_at']}")
    print(f"[timer] state -> {STATE_PATH}")

def mark(phase: str, step: int, note: str = ""):
    if not STATE_PATH.exists():
        start()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    now = datetime.utcnow()
    elapsed = now - datetime.fromisoformat(state["started_at"])
    state["step"] = step
    state["phase"] = phase
    state["milestones"].append({
        "ts": now.isoformat(),
        "elapsed_s": elapsed.total_seconds(),
        "phase": phase,
        "step": step,
        "note": note,
    })
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    h, rem = divmod(int(elapsed.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    print(f"[timer] {h:02d}:{m:02d}:{s:02d} | step={step} | phase={phase} | {note}")

def finish():
    if not STATE_PATH.exists():
        print("[timer] never started")
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    elapsed = datetime.utcnow() - datetime.fromisoformat(state["started_at"])
    h, rem = divmod(int(elapsed.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    print(f"[timer] FINISHED {h:02d}:{m:02d}:{s:02d} | total steps={state['step']} | phases={len(state['milestones'])}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: _refactor_timer.py start|mark <phase> <step> [note]|finish")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "start":
        start()
    elif cmd == "mark":
        mark(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "finish":
        finish()
    else:
        print(f"unknown cmd: {cmd}")
