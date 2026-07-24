#!/usr/bin/env python3.14
"""10-hour refactor timer wrapper for AgentHarness."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

TEN_HOURS = 10 * 3600
STATE_PATH = Path(__file__).with_name("_refactor_timer_state.json")
AgentHarness_PATH = Path(__file__).parent


def ensure_started():
    if not STATE_PATH.exists():
        state = {
            "started_at": datetime.utcnow().isoformat(),
            "step": 0,
            "phase": "init",
            "milestones": [],
            "target_seconds": TEN_HOURS,
        }
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[10h_timer] started target: {TEN_HOURS}s")


def mark(phase: str, step: int, note: str = ""):
    ensure_started()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    now = datetime.utcnow()
    elapsed = (now - datetime.fromisoformat(state["started_at"])).total_seconds()
    state["step"] = step
    state["phase"] = phase
    state["milestones"].append({
        "ts": now.isoformat(),
        "elapsed_s": elapsed,
        "phase": phase,
        "step": step,
        "note": note,
    })
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    remaining = max(0, int(TEN_HOURS - elapsed))
    rh, rm = divmod(remaining, 3600)
    rmm, rs = divmod(rm, 60)
    print(f"[10h_timer] {h:02d}:{m:02d}:{s:02d} | step={step} | {phase} | remaining ~{rh:02d}:{rmm:02d}:{rs:02d}")


def run_pytest():
    print("[10h_timer] running full test suite...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line", "--ignore=tests/test_scheduler.py"],
        cwd=str(AgentHarness_PATH),
        capture_output=True,
        text=True,
    )
    passed = "passed" in result.stdout
    failed = "failed" in result.stdout
    print(f"[10h_timer] pytest exit={result.returncode} passed={passed} failed={failed}")
    if result.stdout:
        print(result.stdout[-500:])
    if result.returncode != 0:
        print("[10h_timer] WARNING: test suite did not pass cleanly")
    return result.returncode == 0


def summary():
    ensure_started()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    elapsed = (datetime.utcnow() - datetime.fromisoformat(state["started_at"])).total_seconds()
    print("=== 10-HOUR TIMER SUMMARY ===")
    print(f"Started : {state['started_at']}")
    print(f"Elapsed : {int(elapsed)}s ({elapsed/3600:.2f}h)")
    print(f"Target  : {TEN_HOURS}s ({TEN_HOURS/3600:.1f}h)")
    print(f"Remaining: {max(0, int(TEN_HOURS - elapsed))}s")
    print(f"Steps   : {state.get('step', 0)}")
    print(f"Phase   : {state.get('phase', 'N/A')}")
    print(f"Milestones: {len(state.get('milestones', []))}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        summary()
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "mark" and len(sys.argv) >= 4:
        mark(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "pytest":
        run_pytest()
    elif cmd == "summary":
        summary()
    else:
        print("usage: 10h_timer.py [mark <phase> <step> [note]|pytest|summary]")
        sys.exit(1)
