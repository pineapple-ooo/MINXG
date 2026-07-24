"""Web UI Server - premium browser-based interface for AgentHarness.

Provides:
  - Full chat interface with streaming responses
  - Tool call / thinking visualization (expandable gray rows)
  - Code block rendering with copy button
  - Inline emphasis (bold/italic/highlight/quote) from AI
  - Multi-agent company mode toggle pill
  - Model selector per provider
  - File/zip attach (+ button)
  - Multi-session memory (localStorage)

Runs a local HTTP + WebSocket server. Access via browser at:
  http://localhost:PORT
"""
import asyncio
import json
import os
import logging
from typing import Any, Dict, Optional
from pathlib import Path
from aiohttp import web, WSMsgType
from multiling.constants import WEB_UI_DEFAULT_PORT

# Enable logging (force-set level in case basicConfig was already called)
logging.getLogger().setLevel(logging.INFO)
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
logger = logging.getLogger(__name__)

# ── Load embedded INDEX_HTML ──
_INDEX_PATH = Path(__file__).with_name('_index.html')
if _INDEX_PATH.exists():
    INDEX_HTML = _INDEX_PATH.read_text(encoding='utf-8')
else:
    raise RuntimeError("Missing _index.html. Run 'python -m minxg.tools.extract_index' to generate it.")

# ── Emphasis markers system prompt ──
EMPHASIS_PROMPT = {"role": "system", "content": """You are AgentHarness, an expert AI assistant. When you write, you MUST use the following emphasis markers to make your responses more readable and engaging:

1. **bold** for key terms, important concepts, and things you want to stand out
2. *italic* for subtle emphasis, technical terms, or foreign words
3. ==highlight== for the most critical information, warnings, or key takeaways
4. >quote for quoting the user's code or questions back to them
5. ~~strikethrough~~ for indicating deprecated or incorrect information
6. `code` for inline code snippets, commands, file paths, and variable names
7. ```language\\n...\\n``` for code blocks (always specify the language)
8. [link text](url) for references

Always use these markers naturally. They make your output clearer and more professional. Never explain that you're using them — just use them."""}

# ═══════════════════════════════════════════════════════════════════
#  Web UI Server
# ═══════════════════════════════════════════════════════════════════

class WebUIServer:
    """Local HTTP + WebSocket server for browser-based AgentHarness interface."""

    def __init__(self, orchestrator, host="0.0.0.0", port=WEB_UI_DEFAULT_PORT):
        self.orch = orchestrator
        self.host = host
        self.port = port
        self.clients = set()
        self._aborting_sessions: set = set()
        self._app = None
        self._uploads_dir = Path.home() / '.minxg' / 'uploads'
        self._uploads_dir.mkdir(parents=True, exist_ok=True)

    def _build_app(self):
        app = web.Application()
        app.router.add_get("/", self._index_handler)
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/api/models", self._models_handler)
        app.router.add_post("/api/upload", self._upload_handler)
        return app

    async def _index_handler(self, request):
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def _ws_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json({
            "type": "init",
            "model": getattr(self.orch, "ai_model", "unknown"),
        })
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(ws, data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error("WS error: %s", ws.exception())
        finally:
            self.clients.discard(ws)
        return ws

    # ── API: model list ──
    async def _models_handler(self, _request):
        try:
            models = self.orch.fetch_models()
        except Exception:
            models = []
        result = []
        for m in models:
            if isinstance(m, (tuple, list)):
                result.append({"value": m[0], "display": m[1] if len(m) > 1 else m[0], "provider": self._split_provider(m[0])})
            elif isinstance(m, str):
                result.append({"value": m, "display": m, "provider": self._split_provider(m)})
            else:
                result.append({"value": str(m), "display": str(m), "provider": "other"})
        return web.json_response({"models": result})

    @staticmethod
    def _split_provider(model_str):
        if ':' in model_str:
            return model_str.split(':', 1)[0]
        if '/' in model_str:
            return model_str.split('/', 1)[0]
        return "Default"

    # ── API: file upload ──
    async def _upload_handler(self, request):
        try:
            reader = await request.multipart()
            files = []
            async for part in reader:
                if part.name != 'file' or not part.filename:
                    continue
                filepath = self._uploads_dir / part.filename
                chunks = []
                async for chunk in part:
                    chunks.append(chunk)
                data = b''.join(chunks)
                filepath.write_bytes(data)
                files.append({"name": part.filename, "size": len(data), "path": str(filepath)})
        except Exception as e:
            logger.warning("upload failed: %s", e)
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"files": files})

    # ── Message routing ──
    async def _handle_message(self, ws, data):
        msg_type = data.get("type", "")
        if msg_type == "chat":
            reasoning_effort = data.get("reasoning_effort") or None
            await self._handle_chat(ws, data.get("message", ""), data.get("history", []), data.get("company", False), reasoning_effort)
        elif msg_type == "abort":
            self._aborting_sessions.add(ws._minxg_session or id(ws))
            await ws.send_json({"type": "abort_ok"})
        elif msg_type == "get_agents":
            await self._send_agent_status(ws)
        elif msg_type == "get_memory":
            await self._send_memory(ws)
        elif msg_type == "get_settings":
            await self._send_settings(ws)

    async def _handle_chat(self, ws, message, history, company_mode=False, reasoning_effort=None):
        """Stream chat. Client owns history — no server-side memory."""
        if not message:
            return
        if message.startswith("/"):
            if message.strip() in ("/exit", "/quit"):
                await ws.send_json({"type": "text", "content": "Session ended."})
                await ws.send_json({"type": "done"})
                return
            if message.startswith("/plan"):
                goal = message[5:].strip() or "Analyze the current project"
                await self._handle_plan(ws, goal)
                return

        messages = [EMPHASIS_PROMPT] + list(history) + [{"role": "user", "content": message}]
        final_text = ""
        _ws_key = id(ws)  # identity key for abort lookup
        try:
            async for event in self.orch.chat_stream_with_history(messages, reasoning_effort=reasoning_effort):
                if _ws_key in self._aborting_sessions:
                    self._aborting_sessions.discard(_ws_key)
                    await ws.send_json({"type": "abort_ok"})
                    await ws.send_json({"type": "done", "final_text": final_text + " [user stopped]"})
                    break
                et = event.get("type", "")
                if et == "text":
                    final_text += event.get("content", "")
                    await ws.send_json({"type": "text", "content": event.get("content", "")})
                elif et == "thinking":
                    await ws.send_json({"type": "thinking"})
                elif et == "tool_call":
                    await ws.send_json({
                        "type": "tool_call",
                        "name": event.get("name", "?"),
                        "args": event.get("args", {}),
                    })
                elif et == "tool_result":
                    name = event.get("name", "?")
                    result = str(event.get("result", ""))
                    await ws.send_json({
                        "type": "tool_result",
                        "name": name,
                        "result": result[:5000],
                        "diff": name in ("file", "patch", "git"),
                    })
                elif et == "done":
                    await ws.send_json({"type": "done", "final_text": final_text})
                elif et == "error":
                    await ws.send_json({"type": "error", "message": event.get("message", "Unknown error")})
                    await ws.send_json({"type": "done"})
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.send_json({"type": "done"})
        finally:
            await self._broadcast_agent_status()

    async def _handle_plan(self, ws, goal):
        """Run company-mode multi-agent collaboration."""
        try:
            from multiling.commander.council import CompanyOrchestrator
            await ws.send_json({
                "type": "text",
                "content": f"Starting company-mode collaboration for: {goal}\n\n",
            })
            orch = CompanyOrchestrator(
                llm_complete=self.orch.llm_complete,
                llm_with_model=self.orch.llm_complete_with_model,
                fetch_models_fn=self.orch.fetch_models,
            )
            result = orch.run(goal)
            await ws.send_json({
                "type": "text",
                "content": result.get("completion", "Done."),
            })
            await ws.send_json({"type": "done"})
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.send_json({"type": "done"})

    # ── Status endpoints ──
    async def _send_agent_status(self, ws):
        await ws.send_json({"type": "agent_status", "groups": {}})

    async def _send_memory(self, ws):
        await ws.send_json({"type": "memory", "session_id": "web", "working": 0, "episodic": 0, "semantic": 0, "recent_turns": []})

    async def _send_settings(self, ws):
        await ws.send_json({"type": "settings", "provider": getattr(self.orch, 'ai_provider', 'sensenova'), "model": getattr(self.orch, 'ai_model', 'unknown'), "base_url": "", "tools": []})

    async def _broadcast_agent_status(self):
        """Broadcast agent status to all connected clients."""
        for ws in self.clients:
            try:
                await ws.send_json({"type": "agent_status", "groups": {}})
            except Exception:
                pass

    async def run(self):
        """Start the server. Blocks until interrupted."""
        from aiohttp import web
        app = self._build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"Web UI: http://{self.host}:{self.port}")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()


def launch_web_ui(orchestrator, host="0.0.0.0", port=WEB_UI_DEFAULT_PORT):
    """Launch the web UI server."""
    server = WebUIServer(orchestrator, host=host, port=port)
    asyncio.run(server.run())