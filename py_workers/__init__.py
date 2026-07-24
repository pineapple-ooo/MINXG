"""Backward-compat alias: py_workers -> agent_harness.

The package was renamed from py_workers to agent_harness in v0.0.2. This stub
keeps `import py_workers` and `from py_workers.<x>` working by exposing
the same surface but routing every attribute lookup through agent_harness so
nothing forks.

The trick used here: py_workers is registered in sys.modules as its OWN
module (not agent_harness), with its OWN __getattr__. py_workers.X therefore
goes through this file's getattr, not agent_harness's. That way the legacy
short pillar names (scalar, io, dispatch, aggregate, transform) and the
math pillars (ga, cat, ...) all resolve correctly here, in addition to
the canonical agent_harness paths.
"""
from __future__ import annotations

import importlib
import sys

import agent_harness as _agent_harness


__all__ = getattr(_agent_harness, "__all__", ())
VERSION = getattr(_agent_harness, "VERSION", "0.11.0")

PILLAR_MODULE_SET = {
    "scalar": "five_pillars.scalar",
    "aggregate": "five_pillars.aggregate",
    "io": "five_pillars.io",
    "dispatch": "five_pillars.dispatch",
    "transform": "five_pillars.transform",
}
MATH_PILLAR_NAMES = ("ga", "cat", "infogeo", "topo", "chaos", "fiber")
PILLAR_KEYS = set(PILLAR_MODULE_SET.keys())


def __getattr__(name: str):
    # py_workers.<pillar> -> agent_harness.five_pillars.<pillar>
    if name in PILLAR_MODULE_SET:
        mod = importlib.import_module(f"agent_harness.{PILLAR_MODULE_SET[name]}")
        sys.modules[f"py_workers.{name}"] = mod
        return mod
    # py_workers.<math> -> agent_harness.<math>
    if name in MATH_PILLAR_NAMES:
        mod = importlib.import_module(f"agent_harness.{name}")
        sys.modules[f"py_workers.{name}"] = mod
        return mod
    # py_workers.five_pillars -> agent_harness.five_pillars
    if name == "five_pillars":
        return importlib.import_module("agent_harness.five_pillars")
    # Anything else: delegate to agent_harness.
    if hasattr(_agent_harness, name):
        return getattr(_agent_harness, name)
    raise AttributeError(f"module 'py_workers' has no attribute {name!r}")


def __dir__():
    base = set(getattr(_agent_harness, "__all__", ()))
    base.update(PILLAR_KEYS)
    base.update(MATH_PILLAR_NAMES)
    base.update({"five_pillars", "VERSION", "cap", "operators"})
    return sorted(base)
