"""agent_harness.screen.capture.termux_backend — Termux:API screencap.

Uses the termux-api binary from the `termux-api` pkg to grab screenshots.
Requires the Termux:API Android companion app to be installed + running.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


TERMUX_API_BIN = "/data/data/com.termux/files/usr/bin/termux-api"


def _resolve_termux_api_bin() -> str | None:
    """Return the best available termux-api executable path."""
    return shutil.which("termux-api") or (TERMUX_API_BIN if Path(TERMUX_API_BIN).exists() else None)


def termux_api_available() -> bool:
    bin_path = _resolve_termux_api_bin()
    if not bin_path:
        return False
    try:
        r = subprocess.run([bin_path, "--help"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

def termux_api_screencap(dest_path: str) -> dict:
    """Grab screen via 'termux-api screencap'.

    The termux-api pkg provides this via a socket to the companion app.
    Returns: {path, format, ok, error?, timestamp}
    """
    out = {"source": "termux_api", "ok": False}
    bin_path = _resolve_termux_api_bin()
    if not bin_path or not termux_api_available():
        out["error"] = "termux-api binary not available; install termux-api pkg + companion app"
        return out

    dp = Path(dest_path)
    dp.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([bin_path, "screencap", str(dp)],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not dp.exists():
        out["error"] = f"screencap failed: {r.stderr[:300]}"
        return out

    try:
        from PIL import Image
        img = Image.open(str(dp))
        out.update(path=str(dp), width=img.size[0], height=img.size[1],
                   format="PNG", ok=True, timestamp=time.time())
    except Exception as e:
        out["error"] = f"PIL error: {e}"
    return out
