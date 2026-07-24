"""
agent_harness/server.py — HTTP RPC server for agent_harness v1.0.0

  GET  /health       -> {"status":"ok", "version":"1.0.0", "registered_workers":[...]}
  GET  /tools        -> {"workers": {"fs_io": [...], ...}}
  POST /rpc          -> body: {"worker":"fs_io","tool":"read_file","params":{"path":"..."}}
"""
from __future__ import annotations
import os
import sys
import json
import asyncio
import logging
import argparse
import time
from typing import Dict, Any, List

# Central constant — keep in sync with multiling/constants.py WORKERS_DEFAULT_PORT
WORKERS_DEFAULT_PORT = 19001

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(levelname)-7s | %(message)s',
                    handlers=[logging.StreamHandler(sys.stderr)])
log = logging.getLogger("py_workers.server")

ALL_WORKERS: Dict[str, type] = {}


def _discover_workers():
    """Import and register all worker classes."""
    _specs = [
        ("fs_io", "agent_harness.five_pillars.io.fs_io", "FsIoWorker"),
        ("fs_copy", "agent_harness.five_pillars.io.fs_copy", "FsCopyWorker"),
        ("fs_search", "agent_harness.five_pillars.io.fs_search", "FsSearchWorker"),
        ("system", "agent_harness.five_pillars.dispatch.system", "SystemWorker"),
        ("network", "agent_harness.five_pillars.io.network", "NetworkWorker"),
        ("sh_query", "agent_harness.five_pillars.dispatch.sh_query", "ShQueryWorker"),
        ("sh_exec", "agent_harness.five_pillars.dispatch.sh_exec", "ShExecWorker"),
        ("limits_lock", "agent_harness.five_pillars.dispatch.limits_lock", "LimitsLockWorker"),
        ("limits_break", "agent_harness.five_pillars.dispatch.limits_break", "LimitsBreakWorker"),
        ("text_tools", "agent_harness.five_pillars.scalar.text_tools", "TextToolsWorker"),
        ("encoding_tools", "agent_harness.five_pillars.scalar.encoding_tools", "EncodingToolsWorker"),
        ("math_tools", "agent_harness.five_pillars.scalar.math_tools", "MathToolsWorker"),
        ("datetime_tools", "agent_harness.five_pillars.scalar.datetime_tools", "DateTimeToolsWorker"),
        ("ai_tools", "agent_harness.five_pillars.transform.ai_tools", "AiToolsWorker"),
        ("media_tools", "agent_harness.five_pillars.io.media_tools", "MediaToolsWorker"),
        ("crypto_tools", "agent_harness.five_pillars.aggregate.crypto_tools", "CryptoToolsWorker"),
        ("db_tools", "agent_harness.five_pillars.io.db_tools", "DbToolsWorker"),
        ("web_tools", "agent_harness.five_pillars.io.web_tools", "WebToolsWorker"),
        ("cloud_tools", "agent_harness.five_pillars.io.cloud_tools", "CloudToolsWorker"),
        ("security_tools", "agent_harness.five_pillars.dispatch.security_tools", "SecurityToolsWorker"),
        ("process_tools", "agent_harness.five_pillars.dispatch.process_tools", "ProcessToolsWorker"),
        ("platform", "agent_harness.five_pillars.dispatch.platform_tools", "PlatformWorker"),
        ("notify", "agent_harness.five_pillars.dispatch.notify_tools", "NotifyWorker"),
        ("string", "agent_harness.five_pillars.scalar.string_tools", "StringWorker"),
        ("color", "agent_harness.five_pillars.scalar.color_tools", "ColorWorker"),
        ("markdown", "agent_harness.five_pillars.scalar.markdown_tools", "MarkdownWorker"),
        ("archive", "agent_harness.five_pillars.io.archive_tools", "ArchiveWorker"),
        ("network_adv", "agent_harness.five_pillars.io.network_adv", "NetworkAdvWorker"),
        ("dev_tools", "agent_harness.five_pillars.dispatch.dev_tools", "DevToolsWorker"),
        ("media_adv", "agent_harness.five_pillars.io.media_adv", "MediaAdvWorker"),
        ("adb", "agent_harness.five_pillars.dispatch.adb_tools", "AdbWorker"),
        ("root", "agent_harness.five_pillars.dispatch.root_tools", "RootWorker"),
        ("operator", "agent_harness.operators", "OperatorWorker"),
    ]
    for worker_id, module_path, class_name in _specs:
        try:
            mod = __import__(module_path, fromlist=[class_name])
            cls = getattr(mod, class_name)
            ALL_WORKERS[worker_id] = cls
        except (ImportError, ModuleNotFoundError, AttributeError) as e:
            log.debug("Skip worker %s: %s", worker_id, e)


async def start_server(host: str = "127.0.0.1", port: int = WORKERS_DEFAULT_PORT,
                       workers: List[str] = None) -> None:
    """Start the HTTP RPC server."""
    from aiohttp import web
    from .base import WorkerRegistry

    if not ALL_WORKERS:
        _discover_workers()

    registry = WorkerRegistry()
    selected = workers or list(ALL_WORKERS.keys())
    for name in selected:
        if name not in ALL_WORKERS:
            continue
        registry.register(ALL_WORKERS[name]())

    async def health(req):
        total_tools = sum(len(w.tools) for w in registry.workers.values())
        return web.json_response({
            "status": "ok", "worker": "py", "version": "1.0.0",
            "registered_workers": list(registry.workers.keys()),
            "port": port, "total_tools": total_tools,
            "uptime_hint": "see /stats"
        })

    async def tools(req):
        worker_filter = req.match_info.get("worker") or req.query.get("worker")
        if worker_filter:
            w = registry.get(worker_filter)
            data = {worker_filter: w.list_tools() if w else []}
        else:
            data = {wid: w.list_tools() for wid, w in registry.workers.items()}
        return web.json_response({"workers": data})

    async def stats(req):
        worker_filter = req.match_info.get("worker")
        if worker_filter:
            w = registry.get(worker_filter)
            data = {worker_filter: w.statistics() if w else {}}
        else:
            data = {wid: w.statistics() for wid, w in registry.workers.items()}
        return web.json_response({"workers": data})

    async def rpc(req):
        t0 = time.time()
        try:
            body = await req.json()
        except json.JSONDecodeError as e:
            return web.json_response({"status": "error", "error": f"invalid JSON: {e}"}, status=400)
        wid = body.get("worker", "")
        tool_name = body.get("tool", "")
        params = body.get("params", {}) or {}
        timeout = float(body.get("timeout", 60))
        if not wid or not tool_name:
            return web.json_response({"status": "error", "error": "worker and tool required"}, status=400)
        try:
            result = await asyncio.wait_for(registry.call(wid, tool_name, params), timeout=timeout)
            elapsed = round((time.time() - t0) * 1000, 2)
            return web.json_response({
                "status": result.get("status", "success"),
                "worker": wid, "tool": tool_name,
                "elapsed_ms": elapsed,
                "result": {k: v for k, v in result.items() if k != "status"},
            })
        except asyncio.TimeoutError:
            return web.json_response({"status": "error", "worker": wid, "tool": tool_name,
                                     "error": f"timeout after {timeout}s"}, status=408)
        except Exception as e:
            return web.json_response({"status": "error", "worker": wid, "tool": tool_name,
                                     "error": str(e)}, status=500)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/tools", tools)
    app.router.add_get("/tools/{worker}", tools)
    app.router.add_get("/stats", stats)
    app.router.add_get("/stats/{worker}", stats)
    app.router.add_post("/rpc", rpc)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port, reuse_address=True)
    try:
        await site.start()
    except OSError:
        log.error("Port %d already in use or permission denied", port)
        sys.exit(1)
    total_tools = sum(len(w.tools) for w in registry.workers.values())
    log.info("   workers (%d): %s", len(registry.workers), ", ".join(registry.workers.keys()))
    log.info("   total_tools: %d", total_tools)
    log.info("   endpoints: GET /health /tools /stats (/tools/{worker} /stats/{worker}), POST /rpc")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="py_workers HTTP RPC server v1.0.0")
    parser.add_argument("--host", default=os.environ.get("WORKER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORKER_PORT", str(WORKERS_DEFAULT_PORT))))
    parser.add_argument("--workers", nargs="*", default=None,
                        help="Specific workers to start (default: all)")
    args = parser.parse_args()
    try:
        asyncio.run(start_server(args.host, args.port, args.workers))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()