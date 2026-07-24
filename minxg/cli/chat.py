"""ChatTui — wires the agent orchestrator into the new prompt_toolkit TUI.

This is the integration point between:
  * ``minxg.cli.app.Tui`` — the generic prompt_toolkit surface
  * ``multiligua_cli.tui_chat._ensure_orchestrator`` / ``_stream`` — the
    existing AgentHarness streaming + slash-command logic

The class is deliberately thin: it owns the orchestrator instance and the
active session id, translates model stream events into ``Tui._output``
lines, and exposes ``run()`` as the drop-in replacement for
``multiligua_cli.tui_chat.tui_chat``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from minxg.cli.app import MessageResult, Tui
from minxg.cli.commands import all_commands


class ChatTui:
    """High-level chat shell that glues orchestrator streaming to the TUI."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        company_mode: bool = False,
        reasoning_effort: str = "medium",
        tool_names: Optional[List[str]] = None,
        initial_status: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config = config
        self.company_mode = company_mode
        self.reasoning_effort = reasoning_effort
        self._tool_names = tool_names or []

        # Persistent session id across relaunches (same id = same memory file)
        self._session_id = session_id or self._make_session_id()

        # Build orchestrator from config
        self.orch = self._ensure_orchestrator(config)

        # Derive status fields for the TUI status bar
        ai = config.get("ai", {})
        provider = ai.get("provider", "local")
        model = ai.get("model") or self._provider_default_model(provider)
        nexus = bool(getattr(self.orch, "_nexus_enabled", False))

        status = dict(initial_status or {})
        status.setdefault("provider", provider)
        status.setdefault("model", model or "unset")
        status.setdefault("nexus", "NEXUS" if nexus else "─")
        status.setdefault("reason", reasoning_effort)

        # Build underlying prompt_toolkit app
        self._tui = Tui(
            initial_status=status,
            tool_names=self._tool_names,
            on_user_message=self._on_user_message,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> int:
        self._tui.run()
        return 0

    # ------------------------------------------------------------------
    # Orchestrator / config helpers
    # ------------------------------------------------------------------
    def _ensure_orchestrator(self, config: Dict[str, Any]):
        try:
            from multiling.orchestrator import NexusOrchestrator
            ai = config.get("ai", {})
            model = ai.get("model")
            if isinstance(model, str) and model.startswith("/"):
                return None
            return NexusOrchestrator(
                ai_base_url=ai.get("base_url"),
                ai_api_key=ai.get("api_key"),
                ai_provider=ai.get("provider", "local"),
                ai_model=model,
            )
        except Exception as exc:
            self._tui._append(f"[error]Orchestrator init failed: {exc}[/error]")
            return None

    @staticmethod
    def _provider_default_model(provider: str) -> str:
        try:
            from multiligua_cli.providers import AI_PROVIDERS
            info = AI_PROVIDERS.get(provider, {})
            return info.get("default_model", "")
        except Exception:
            return ""

    @staticmethod
    def _make_session_id() -> str:
        try:
            from pathlib import Path
            import uuid
            session_file = Path.home() / ".minxg" / "session_id"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            if session_file.exists():
                sid = session_file.read_text(encoding="utf-8").strip()
                if sid:
                    return sid
            sid = f"minxg_{uuid.uuid4().hex[:12]}"
            session_file.write_text(sid, encoding="utf-8")
            return sid
        except Exception:
            import uuid
            return f"minxg_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------
    def _on_user_message(self, text: str) -> MessageResult:
        if not text or not self.orch:
            return None

        if text.startswith("/"):
            return self._dispatch_slash(text)

        # Try streaming path first; fall back to sync complete.
        try:
            return self._stream_response(text)
        except RuntimeError:
            return self._sync_send(text)

    def _dispatch_slash(self, text: str) -> Optional[str]:
        cmd = text.lower().split()[0]
        slash = next(
            (c for c in all_commands() if c.name.lower() == cmd),
            None,
        )
        if slash:
            try:
                return str(slash.handler(text))
            except Exception as exc:
                return f"[error]Command error: {exc}[/error]"
        return f"[warning]Unknown command:[/warning] {cmd}"

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def _stream_response(self, user_message: str) -> str:
        if not hasattr(self.orch, "chat_stream"):
            return self._sync_send(user_message)

        final_parts: List[str] = []
        try:
            async for event in self.orch.chat_stream(
                user_message,
                session_id=self._session_id,
                company_mode=self.company_mode,
            ):
                await self._handle_stream_event(event, final_parts)
        except Exception as exc:
            self._tui._append(f"[error]{exc}[/error]")
        return "".join(final_parts)

    async def _handle_stream_event(
        self, event: Dict[str, Any], final_parts: List[str]
    ) -> None:
        event_type = event.get("type")
        if event_type in ("text", "token"):
            chunk = event.get("content", "")
            final_parts.append(chunk)
            self._tui._append(chunk)
        elif event_type == "thinking":
            self._tui._append("[dim]thinking...[/dim]")
            final_parts.append("[thinking]" + event.get("content", "") + "[/thinking]")
        elif event_type == "tool_call":
            self._render_tool_card(
                event.get("name", ""),
                event.get("arguments", event.get("args", {})),
                elapsed_ms=event.get("elapsed_ms", 0),
                warning=event.get("warning", ""),
            )
        elif event_type == "tool_result":
            self._render_tool_result(event)
        elif event_type == "error":
            self._tui._append(f"[error]{event.get('message', 'error')}[/error]")

    def _render_tool_result(self, event: Dict[str, Any]) -> None:
        name = event.get("name", "?")
        elapsed = int(event.get("elapsed_ms", 0))
        result = event.get("result", {})
        try:
            from multiligua_cli.tui_chat import _render_tool_card
            _render_tool_card(name, result, elapsed_ms=elapsed)
            return
        except Exception:
            pass
        self._tui._append(f"[cyan]⟦ {name} ⟧ ({elapsed}ms)[/cyan]")

    def _sync_send(self, user_message: str) -> str:
        try:
            response = self.orch.chat_complete(
                user_message,
                session_id=self._session_id,
                company_mode=self.company_mode,
            )
            if isinstance(response, dict):
                return response.get("content", "")
            return str(response)
        except Exception as exc:
            self._tui._append(f"[error]{exc}[/error]")
            return ""
