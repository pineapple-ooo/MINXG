"""minxg_ui.web — rewritten unified web UI.

Single FastAPI app with embedded SPA. Routes:
  * ``/`` — main app (chat + dashboard + agents + settings)
  * ``/api/chat`` — chat proxy
  * ``/api/dashboard/*`` — token stats, context state, memory stats
  * ``/api/agents/*`` — agent list, workflow graph, savepoints
  * ``/api/system/*`` — health, config, version
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[misc,assignment]
    Request = None  # type: ignore[misc,assignment]
    HTMLResponse = None  # type: ignore[misc,assignment]
    JSONResponse = None  # type: ignore[misc,assignment]
    Response = None  # type: ignore[misc,assignment]
    CORSMiddleware = None  # type: ignore[misc,assignment]

try:
    from minxg.context.compression import (
        AutoCompressor,
        CompressedContext,
        compress,
        decompress,
        detect_context_window,
        estimate_tokens,
        usage_ratio,
    )
    from minxg.context.memory import DayMemory
    from minxg.context.model_probe import ModelContextProbe
    from minxg.context.token_tracker import TokenBudgetTracker
    from minxg.context.dev_utils import SavepointManager, diff_messages, DevToolbox
    _CONTEXT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CONTEXT_AVAILABLE = False

if not _FASTAPI_AVAILABLE:
    app = None  # type: ignore[assignment]
    _STATIC_AVAILABLE = False
else:
    app = FastAPI(
        title="AgentHarness Web",
        description="Unified web UI: chat, dashboard, agents, settings.",
        version="0.19.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    _probe = ModelContextProbe() if _CONTEXT_AVAILABLE else None
    _tracker = TokenBudgetTracker() if _CONTEXT_AVAILABLE else None
    _memory = DayMemory() if _CONTEXT_AVAILABLE else None
    _savepoints = SavepointManager() if _CONTEXT_AVAILABLE else None
    _chat_history: List[Dict[str, Any]] = []
    _auto = AutoCompressor(probe=_probe) if _CONTEXT_AVAILABLE else None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _json(data: Any, status: int = 200) -> JSONResponse:
        return JSONResponse(data, status_code=status)

    def _ok(data: Any = None) -> JSONResponse:
        return _json({"ok": True, "data": data})

    def _err(msg: str, status: int = 400) -> JSONResponse:
        return _json({"ok": False, "error": msg}, status=status)

    # ------------------------------------------------------------------ #
    # Chat routes
    # ------------------------------------------------------------------ #

    @app.get("/api/chat/history")
    async def chat_history() -> JSONResponse:
        return _json({"history": _chat_history})

    @app.post("/api/chat")
    async def chat_send(request: Request) -> JSONResponse:
        body = await request.json()
        message = (body.get("message") or "").strip()
        if not message:
            return _err("empty message")

        _chat_history.append({"role": "user", "content": message})

        # Placeholder reply — swap for real orchestrator call
        reply = f"[echo] {message}"
        _chat_history.append({"role": "assistant", "content": reply})

        if _tracker:
            from minxg.context.compression import estimate_tokens
            inp = estimate_tokens([{"role": "user", "content": message}])
            out = estimate_tokens([{"role": "assistant", "content": reply}])
            _tracker.record_turn(inp, out)

        return _json({"reply": reply})

    @app.delete("/api/chat")
    async def chat_clear() -> JSONResponse:
        _chat_history.clear()
        return _ok({"message": "chat cleared"})

    # ------------------------------------------------------------------ #
    # Dashboard routes
    # ------------------------------------------------------------------ #

    @app.get("/api/dashboard/tokens")
    async def dashboard_tokens() -> JSONResponse:
        if not _tracker:
            return _json({"input_tokens": 0, "output_tokens": 0, "compressed_input_tokens": 0})
        return _json(_tracker.snapshot())

    @app.get("/api/dashboard/context")
    async def dashboard_context() -> JSONResponse:
        if not _CONTEXT_AVAILABLE:
            return _json({"tier": "max", "compressed_tokens": 0, "compression_ratio": 1.0})
        ctx = compress(_chat_history, budget_tokens=8192)
        return _json(ctx.to_dict())

    @app.get("/api/dashboard/memory")
    async def dashboard_memory() -> JSONResponse:
        data: Dict[str, Any] = {"episodes": 0, "facts": 0, "patterns": 0}
        if _memory:
            try:
                data["episodes"] = len(_memory.store.get_episodes("", limit=1000))
                data["facts"] = len(_memory.store.query_facts())
                data["patterns"] = len(_memory.store.get_patterns("*"))
            except Exception:
                pass
        return _json(data)

    # ------------------------------------------------------------------ #
    # Agent routes
    # ------------------------------------------------------------------ #

    @app.get("/api/agents")
    async def agents_list() -> JSONResponse:
        return _json({
            "agents": [
                {"id": "orchestrator", "name": "Orchestrator", "status": "idle"},
                {"id": "chat", "name": "Chat Worker", "status": "idle"},
                {"id": "compression", "name": "Compression Engine", "status": "idle"},
            ],
            "active": 0,
        })

    @app.get("/api/agents/workflow")
    async def agents_workflow() -> JSONResponse:
        """Return a simple DAG for agent workflow visualization."""
        return _json({
            "nodes": [
                {"id": "in", "label": "Input", "type": "io"},
                {"id": "plan", "label": "Planner", "type": "agent"},
                {"id": "tool", "label": "Tool Runner", "type": "agent"},
                {"id": "compress", "label": "Compressor", "type": "service"},
                {"id": "out", "label": "Output", "type": "io"},
            ],
            "edges": [
                {"from": "in", "to": "plan"},
                {"from": "plan", "to": "tool"},
                {"id": "e1", "from": "tool", "to": "compress"},
                {"from": "compress", "to": "plan"},
                {"from": "plan", "to": "out"},
            ],
        })

    @app.get("/api/agents/savepoints")
    async def agents_savepoints() -> JSONResponse:
        if not _savepoints:
            return _json({"savepoints": []})
        return _json({"savepoints": _savepoints.list_savepoints()})

    @app.post("/api/agents/savepoints")
    async def agents_savepoint_create(request: Request) -> JSONResponse:
        if not _savepoints:
            return _err("savepoints unavailable")
        body = await request.json()
        desc = body.get("description", "")
        sp = _savepoints.save(_chat_history, description=desc)
        return _ok(sp.to_dict())

    @app.post("/api/agents/savepoints/{sp_id}/restore")
    async def agents_savepoint_restore(sp_id: str) -> JSONResponse:
        if not _savepoints:
            return _err("savepoints unavailable")
        try:
            restored = _savepoints.restore(sp_id)
            _chat_history.clear()
            _chat_history.extend(restored)
            return _ok({"restored": len(restored)})
        except KeyError:
            return _err("savepoint not found", status=404)

    @app.get("/api/agents/savepoints/{sp_id}/diff/{other_id}")
    async def agents_savepoint_diff(sp_id: str, other_id: str) -> JSONResponse:
        if not _savepoints:
            return _err("savepoints unavailable")
        try:
            text = _savepoints.diff(sp_id, other_id)
            return _json({"diff": text})
        except KeyError:
            return _err("savepoint not found", status=404)

    # ------------------------------------------------------------------ #
    # System routes
    # ------------------------------------------------------------------ #

    @app.get("/api/system/health")
    async def system_health() -> JSONResponse:
        return _json({
            "status": "ok",
            "context": _CONTEXT_AVAILABLE,
            "fastapi": _FASTAPI_AVAILABLE,
            "version": "0.19.0",
        })

    @app.get("/api/system/version")
    async def system_version() -> JSONResponse:
        try:
            from minxg import __version__
            version = __version__
        except Exception:
            version = "0.19.0"
        return _json({"version": version, "service": "minxg_ui.web"})

    # ------------------------------------------------------------------ #
    # SPA
    # ------------------------------------------------------------------ #

    _INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentHarness</title>
<style>
  :root {
    --bg: #ffffff;
    --panel: #f7f9fb;
    --text: #1f2328;
    --muted: #656d76;
    --border: #d0d7de;
    --accent: #0969da;
    --accent-soft: #ddf4ff;
    --ok: #1a7f37;
    --warn: #9a6700;
    --err: #cf222e;
    --shadow: 0 1px 2px rgba(31,35,40,0.04), 0 1px 3px rgba(31,35,40,0.08);
    --radius: 12px;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --text: #e6edf3;
      --muted: #8b949e;
      --border: #30363d;
      --accent: #58a6ff;
      --accent-soft: #0d1117;
      --shadow: 0 1px 2px rgba(0,0,0,0.3);
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  header {
    position: sticky; top: 0; z-index: 10;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    display: flex; align-items: center; justify-content: space-between;
    box-shadow: var(--shadow);
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand svg { width: 22px; height: 22px; }
  .brand h1 { font-size: 14px; font-weight: 600; margin: 0; }
  nav { display: flex; gap: 6px; }
  nav button {
    background: transparent; color: var(--muted); border: 1px solid transparent;
    padding: 6px 10px; border-radius: 8px; font-size: 13px; cursor: pointer;
  }
  nav button.active { color: var(--text); border-color: var(--border); background: var(--panel); }
  main { height: calc(100% - 52px); overflow: hidden; }
  .view { display: none; height: 100%; overflow: auto; padding: 16px; }
  .view.active { display: block; }
  .grid { display: grid; gap: 12px; }
  .grid-2 { grid-template-columns: repeat(2, 1fr); }
  .grid-3 { grid-template-columns: repeat(3, 1fr); }
  @media (max-width: 860px) {
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px;
    box-shadow: var(--shadow);
  }
  .panel h2 {
    margin: 0 0 10px; font-size: 12px; text-transform: uppercase;
    letter-spacing: .08em; color: var(--muted); font-weight: 600;
  }
  .stat { display: flex; justify-content: space-between; padding: 6px 0; }
  .stat .label { color: var(--muted); font-size: 13px; }
  .stat .value { font-weight: 600; font-variant-numeric: tabular-nums; font-size: 13px; }
  .bar { height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; margin-top: 8px; }
  .bar > i { display: block; height: 100%; background: var(--accent); width: 0%; transition: width .25s; }
  .chat { display: flex; flex-direction: column; gap: 10px; height: calc(100% - 64px); }
  .msg { padding: 10px 12px; border-radius: 10px; max-width: 78%; word-wrap: break-word; font-size: 14px; line-height: 1.5; }
  .msg.user { align-self: flex-end; background: var(--accent-soft); border: 1px solid var(--border); color: var(--text); }
  .msg.assistant { align-self: flex-start; background: var(--panel); border: 1px solid var(--border); }
  .msg.system { align-self: center; background: transparent; border: 1px dashed var(--border); color: var(--muted); font-size: 12px; }
  .composer { display: flex; gap: 8px; margin-top: 10px; }
  .composer input {
    flex: 1; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
    font-size: 14px; background: var(--bg); color: var(--text);
  }
  .composer input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
  .btn {
    padding: 8px 14px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--panel); color: var(--text); font-weight: 600; font-size: 13px; cursor: pointer;
  }
  .btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  .tag {
    display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px;
    border-radius: 999px; font-size: 12px; border: 1px solid var(--border); color: var(--muted);
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .empty { color: var(--muted); font-size: 13px; padding: 10px 0; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
    </svg>
    <h1>AgentHarness</h1>
  </div>
  <nav>
    <button data-view="chat" class="active">Chat</button>
    <button data-view="dashboard">Dashboard</button>
    <button data-view="agents">Agents</button>
    <button data-view="settings">Settings</button>
  </nav>
</header>
<main>
  <section id="view-chat" class="view active">
    <div class="panel" style="height:100%;display:flex;flex-direction:column;">
      <h2>Chat</h2>
      <div id="chat" class="chat"></div>
      <div class="composer">
        <input id="msg" placeholder="Type a message..." autocomplete="off" />
        <button id="send" class="btn primary">Send</button>
      </div>
    </div>
  </section>

  <section id="view-dashboard" class="view">
    <div class="grid grid-3">
      <div class="panel">
        <h2>Tokens</h2>
        <div class="stat"><span class="label">Input</span><span class="value" id="d-in">0</span></div>
        <div class="stat"><span class="label">Output</span><span class="value" id="d-out">0</span></div>
        <div class="stat"><span class="label">Compressed</span><span class="value" id="d-cin">0</span></div>
        <div class="bar"><i id="d-bar"></i></div>
      </div>
      <div class="panel">
        <h2>Context</h2>
        <div class="stat"><span class="label">Tier</span><span class="value" id="d-tier">-</span></div>
        <div class="stat"><span class="label">Messages</span><span class="value" id="d-msgs">0</span></div>
        <div class="stat"><span class="label">Tokens</span><span class="value" id="d-tokens">0</span></div>
        <div class="stat"><span class="label">Ratio</span><span class="value" id="d-ratio">-</span></div>
      </div>
      <div class="panel">
        <h2>Memory</h2>
        <div class="stat"><span class="label">Episodes</span><span class="value" id="m-ep">0</span></div>
        <div class="stat"><span class="label">Facts</span><span class="value" id="m-facts">0</span></div>
        <div class="stat"><span class="label">Patterns</span><span class="value" id="m-pat">0</span></div>
      </div>
    </div>
    <div class="panel" style="margin-top:12px;">
      <h2>Recent tool activity</h2>
      <div id="tool-activity" class="empty">No tool calls yet.</div>
    </div>
  </section>

  <section id="view-agents" class="view">
    <div class="grid grid-2">
      <div class="panel">
        <h2>Agents</h2>
        <table>
          <thead><tr><th>ID</th><th>Name</th><th>Status</th></tr></thead>
          <tbody id="agents-table"></tbody>
        </table>
      </div>
      <div class="panel">
        <h2>Workflow</h2>
        <svg id="workflow-svg" viewBox="0 0 640 260" style="width:100%;height:auto;"></svg>
      </div>
    </div>
    <div class="panel" style="margin-top:12px;">
      <h2>Savepoints</h2>
      <div class="row">
        <button id="sp-create" class="btn">Create savepoint</button>
        <button id="sp-refresh" class="btn">Refresh</button>
      </div>
      <table style="margin-top:10px;">
        <thead><tr><th>ID</th><th>Description</th><th>Messages</th><th>Tokens</th><th>Actions</th></tr></thead>
        <tbody id="savepoints-table"></tbody>
      </table>
    </div>
  </section>

  <section id="view-settings" class="view">
    <div class="panel">
      <h2>Settings</h2>
      <div class="stat"><span class="label">Version</span><span class="value" id="s-ver">-</span></div>
      <div class="stat"><span class="label">Status</span><span class="value" id="s-status">-</span></div>
      <div class="stat"><span class="label">Context module</span><span class="value" id="s-ctx">-</span></div>
      <div class="stat"><span class="label">Model context default</span><span class="value" id="s-model-ctx">-</span></div>
    </div>
  </section>
</main>

<script>
  const $ = (id) => document.getElementById(id);
  const api = (path, opts={}) => fetch(path, opts).then(r => r.json());

  function setView(name) {
    document.querySelectorAll('.view').forEach(el => el.classList.toggle('active', el.id === 'view-' + name));
    document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  }
  document.querySelectorAll('nav button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));

  async function refreshDashboard() {
    const tokens = await api('/api/dashboard/tokens');
    $('d-in').textContent = tokens.input_tokens ?? 0;
    $('d-out').textContent = tokens.output_tokens ?? 0;
    $('d-cin').textContent = tokens.compressed_input_tokens ?? 0;
    const saved = (tokens.input_tokens || 0) - (tokens.compressed_input_tokens || 0);
    const ratio = tokens.input_tokens ? saved / tokens.input_tokens : 0;
    $('d-bar').style.width = (ratio * 100) + '%';

    const ctx = await api('/api/dashboard/context');
    $('d-tier').textContent = ctx.tier ?? '-';
    $('d-msgs').textContent = ctx.compressed_count ?? 0;
    $('d-tokens').textContent = ctx.compressed_tokens ?? 0;
    $('d-ratio').textContent = ctx.compression_ratio ? ctx.compression_ratio.toFixed(2) : '-';

    const mem = await api('/api/dashboard/memory');
    $('m-ep').textContent = mem.episodes ?? 0;
    $('m-facts').textContent = mem.facts ?? 0;
    $('m-pat').textContent = mem.patterns ?? 0;
  }

  async function refreshAgents() {
    const agents = await api('/api/agents');
    const tbody = $('agents-table');
    tbody.innerHTML = '';
    for (const a of agents.agents || []) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="mono">${a.id}</td><td>${a.label || a.name}</td><td>${a.status}</td>`;
      tbody.appendChild(tr);
    }

    const wf = await api('/api/agents/workflow');
    const svg = $('workflow-svg');
    svg.innerHTML = '';
    const nodeMap = {};
    (wf.nodes || []).forEach((n, i) => {
      nodeMap[n.id] = { x: 60 + (i % 3) * 220, y: 60 + Math.floor(i / 3) * 140 };
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.innerHTML = `<rect x="${nodeMap[n.id].x - 50}" y="${nodeMap[n.id].y - 20}" width="100" height="40" rx="8" fill="var(--panel)" stroke="var(--border)"/><text x="${nodeMap[n.id].x}" y="${nodeMap[n.id].y + 5}" text-anchor="middle" font-size="12" fill="var(--text)">${n.label}</text>`;
      svg.appendChild(g);
    });
    (wf.edges || []).forEach(e => {
      const a = nodeMap[e.from], b = nodeMap[e.to];
      if (!a || !b) return;
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', a.x + 50); line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x - 50); line.setAttribute('y2', b.y);
      line.setAttribute('stroke', 'var(--border)'); line.setAttribute('stroke-width', '2');
      svg.appendChild(line);
    });

    const sp = await api('/api/agents/savepoints');
    const tbody2 = $('savepoints-table');
    tbody2.innerHTML = '';
    for (const s of (sp.savepoints || [])) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="mono">${s.id}</td><td>${s.description || ''}</td><td>${s.message_count}</td><td>${s.token_count}</td>
        <td><button class="btn" onclick="restoreSavepoint('${s.id}')">Restore</button></td>`;
      tbody2.appendChild(tr);
    }
  }

  async function refreshSettings() {
    const health = await api('/api/system/health');
    $('s-status').textContent = health.status;
    $('s-ctx').textContent = health.context ? 'loaded' : 'unavailable';
    $('s-ver').textContent = '0.19.0';
    try {
      const modelCtx = await api('/api/dashboard/context');
      $('s-model-ctx').textContent = (modelCtx.metadata && modelCtx.metadata.model) ? modelCtx.metadata.model : 'default';
    } catch (e) { $('s-model-ctx').textContent = 'unknown'; }
  }

  async function send() {
    const input = $('msg');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    const chat = $('chat');
    const user = document.createElement('div');
    user.className = 'msg user'; user.textContent = text; chat.appendChild(user);
    const r = await api('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
    const bot = document.createElement('div');
    bot.className = 'msg assistant'; bot.textContent = r.reply || '(no reply)'; chat.appendChild(bot);
    chat.scrollTop = chat.scrollHeight;
    refreshDashboard();
  }

  async function restoreSavepoint(id) {
    const r = await api(`/api/agents/savepoints/${encodeURIComponent(id)}/restore`, { method: 'POST' });
    if (r.ok) alert('Restored ' + r.restored + ' messages');
    else alert('Failed: ' + r.error);
    refreshAgents();
  }

  $('send').onclick = send;
  $('msg').addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
  $('sp-create').onclick = async () => {
    const desc = prompt('Savepoint description');
    if (!desc) return;
    await api('/api/agents/savepoints', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: desc }) });
    refreshAgents();
  };
  $('sp-refresh').onclick = refreshAgents;

  async function refresh() {
    await refreshDashboard();
    refreshAgents();
    refreshSettings();
  }
  refresh();
  setInterval(refresh, 2000);
</script>
</body>
</html>
"""

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @app.get("/health")
    async def health() -> JSONResponse:
        return _json({
            "status": "ok",
            "service": "minxg_ui.web",
            "context": _CONTEXT_AVAILABLE,
        })

__all__ = ["app"]
