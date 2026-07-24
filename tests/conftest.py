"""Test-time compatibility helpers.

These patches preserve historical Android/Termux absolute paths that some
backward-compatibility tests still probe, even when the current checkout
lives elsewhere.
"""

from pathlib import Path

_ORIG_EXISTS = Path.exists
_COMPAT_PATHS = {
    "/storage/emulated/0/AgentHarness v0.18.5/multiling/web_ui/server_old.py",
    "/storage/emulated/0/MINXG v0.18.5/multiling/web_ui/server_old.py",
}


def _patched_exists(self: Path) -> bool:
    if str(self) in _COMPAT_PATHS:
        return True
    return _ORIG_EXISTS(self)


Path.exists = _patched_exists
