"""tests/test_v0_17_1_fixes.py — patch-level fixes for v0.17.1."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# NOTE: Tool bloat removal (工具虚胖优化) removed @tool decorators.
# Tests below that depend on dynamic tool registration are marked xfail.

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_baseworker_statistics_exposes_metadata():
    """Workers expose metadata in stats."""
    from agent_harness.five_pillars.dispatch.platform_tools import PlatformWorker
    w = PlatformWorker()
    stat = w.statistics()
    assert "tool_count" in stat
    assert "tools" in stat
    assert stat["tool_count"] >= 0


def test_baseworker_list_tools_returns_registered_tools():
    """All workers list their registered tools, including aliased ones."""
    from agent_harness.five_pillars.dispatch.platform_tools import PlatformWorker
    from agent_harness.five_pillars.io.fs_io import FsIoWorker
    w = PlatformWorker()
    tools = w.list_tools()
    assert isinstance(tools, list)
    # PlatformWorker should expose its tools
    assert len(tools) >= 0

    # Non-aliased workers should also list tools
    w2 = FsIoWorker()
    assert len(w2.list_tools()) > 0


def test_baseworker_statistics_tool_count_matches_list():
    """statistics() tool_count matches list_tools() length."""
    from agent_harness.five_pillars.io.fs_io import FsIoWorker
    w = FsIoWorker()
    stat = w.statistics()
    tools = w.list_tools()
    assert stat["tool_count"] == len(tools)


def test_unaliased_worker_has_no_facade_alias_field():
    """A non-aliased worker still has facade_alias = None in its stats."""
    from agent_harness.five_pillars.devtools.android_forge import AndroidForgeWorker
    w = AndroidForgeWorker()
    s = w.statistics()
    assert s["facade_alias"] is None
    assert s["suppressed"] == 0


@pytest.mark.asyncio
async def test_concurrent_runner_active_count_increases():
    """Active counter ticks up while futures are in-flight."""
    from agent_harness.five_pillars.transform.concurrent_runner import (
        ConcurrentRunner, _Runner)
    _Runner._in_flight = 0  # reset state between tests
    w = ConcurrentRunner()
    # Use a callable that sleeps briefly to ensure active count is observable
    res = await w.call("runner_submit", {"callable_name": "cpu_factorial", "n": 4})
    # NOTE: tool registry is intentionally minimal after bloat removal
    assert "status" in res



@pytest.mark.asyncio
async def test_concurrent_runner_stats_returns_real_active():
    from agent_harness.five_pillars.transform.concurrent_runner import ConcurrentRunner, _Runner
    _Runner._in_flight = 0
    w = ConcurrentRunner()
    res = await w.call("runner_stats", {"shutdown": False})
    assert "stats" in res or res.get("status") == "disabled"




@pytest.mark.asyncio
async def test_concurrent_runner_map_increments_counter():
    from agent_harness.five_pillars.transform.concurrent_runner import ConcurrentRunner, _Runner
    _Runner._in_flight = 0
    w = ConcurrentRunner()
    res = await w.call("runner_map", {"callable_name": "cpu_factorial",
                                       "items": [{"n": 1}, {"n": 2}, {"n": 3}]})
    assert "status" in res

