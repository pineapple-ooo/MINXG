"""AgentHarness prompt_toolkit TUI — minimal viable shell.

This is the new TUI entry point.  It replaces the old plain REPL with a
modern split-pane interface: output window, status bar, fixed input.
prompt_toolkit is optional; when missing we fall back to a plain stdin loop.

Streaming support:
  * ``on_user_message`` may return a plain string/dict or an async
    generator / coroutine.
  * When it returns an async iterable, chunks are appended live and the
    output window is invalidated so prompt_toolkit redraws.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Union

try:
    from prompt_toolkit import Application
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        ConditionalContainer,
        Float,
        FloatContainer,
        HSplit,
        Layout,
        Window,
        WindowAlign,
    )
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.widgets import TextArea

    HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_PROMPT_TOOLKIT = False

from minxg.cli.completer import SlashCommandCompleter
from minxg.cli.handlers import (
    handle_clear,
    handle_help,
    handle_status,
    handle_tools,
)
from minxg.cli.history import History
from minxg.cli.theme import build_prompt_toolkit_style


MessageResult = Union[str, Dict[str, Any], AsyncIterator[str], None]


class _TuiBase:
    def __init__(
        self,
        *,
        initial_status: Optional[Dict[str, Any]] = None,
        tool_names: Optional[List[str]] = None,
        on_user_message=None,
    ) -> None:
        self.history = History()
        self._tool_names = tool_names or []
        self._on_user_message = on_user_message
        self._status = dict(initial_status or {})
        self._output: List[str] = []

    def _append(self, text: str) -> None:
        self._output.append(text)
        if len(self._output) > 500:
            self._output = self._output[-500:]
        self._invalidate()

    def _invalidate(self) -> None:
        """Override in prompt_toolkit subclass to trigger redraw."""

    @staticmethod
    def _style(name: str) -> str:
        return name

    def _handle_slash(self, text: str) -> Optional[str]:
        cmd = text.lower().split()[0]
        if cmd in ("/exit", "/quit"):
            return "__EXIT__"
        if cmd == "/help":
            return handle_help()
        if cmd == "/tools":
            return handle_tools([{"name": n, "description": ""} for n in self._tool_names])
        if cmd == "/clear":
            self._output.clear()
            return handle_clear()
        if cmd == "/status":
            return handle_status(self._status)
        return f"[{self._style('warning')}]Unknown command:[/{self._style('warning')}] {cmd}"

    def set_status(self, **kwargs: Any) -> None:
        self._status.update(kwargs)


if HAS_PROMPT_TOOLKIT:
    class Tui(_TuiBase):
        """prompt_toolkit-based TUI with streaming support."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._kb = KeyBindings()
            self._input = TextArea(
                prompt="❯ ",
                multiline=False,
                wrap_lines=False,
                completer=SlashCommandCompleter(self._tool_names),
                complete_while_typing=True,
                key_bindings=self._kb,
            )
            self._build_key_bindings()
            self._app = self._build_app()

        def _invalidate(self) -> None:
            try:
                self._app.invalidate()
            except Exception:
                pass

        def _build_key_bindings(self) -> None:
            @self._kb.add("enter")
            def _enter(event: Any) -> None:
                text = self._input.text.strip()
                self._input.text = ""
                self._handle_text(text)

            @self._kb.add("c-c")
            def _ctrl_c(event: Any) -> None:
                self._input.text = ""
                self._handle_text("/exit")

        def _build_app(self) -> Application:
            output_control = FormattedTextControl(
                text=lambda: "\n".join(self._output),
                focusable=False,
            )
            output_window = Window(
                content=output_control,
                wrap_lines=True,
                always_hide_cursor=True,
                height=Dimension(weight=1),
            )
            status_control = FormattedTextControl(
                text=lambda: " | ".join(f"{k}={v}" for k, v in self._status.items())
            )
            status_bar = Window(
                content=status_control,
                height=1,
                style="class:status",
                align=WindowAlign.LEFT,
            )

            root = HSplit(
                [
                    output_window,
                    status_bar,
                    self._input,
                ]
            )
            container = FloatContainer(
                content=root,
                floats=[
                    Float(
                        ConditionalContainer(
                            content=Window(content=FormattedTextControl(text="")),
                            filter=Condition(lambda: False),
                        )
                    )
                ],
            )
            return Application(
                layout=Layout(container),
                key_bindings=self._kb,
                style=build_prompt_toolkit_style(),
                full_screen=False,
                mouse_support=False,
            )

        def _handle_text(self, text: str) -> None:
            if not text:
                return
            self._append(f"[{self._style('user')}]You:[/{self._style('user')}] {text}")

            if text.startswith("/"):
                handled = self._handle_slash(text)
                if handled == "__EXIT__":
                    self._app.exit()
                    return
                if handled is not None:
                    self._append(handled)
                    return

            if self._on_user_message:
                try:
                    result = self._on_user_message(text)
                except Exception as exc:
                    self._append(
                        f"[{self._style('error')}]Error:[/{self._style('error')}] {exc}"
                    )
                    return
                self._process_result(result)

        def _process_result(self, result: MessageResult) -> None:
            if result is None:
                return
            if asyncio.iscoroutine(result):
                asyncio.create_task(self._run_coroutine(result))
                return
            if hasattr(result, "__aiter__"):
                asyncio.create_task(self._run_async_iterable(result))
                return
            if isinstance(result, str):
                self._append(result)
            elif isinstance(result, dict) and "final_response" in result:
                self._append(result["final_response"])
            else:
                self._append(str(result))

        async def _run_coroutine(self, coro) -> None:
            try:
                result = await coro
            except Exception as exc:
                self._append(f"[{self._style('error')}]Error:[/{self._style('error')}] {exc}")
                return
            self._process_result(result)

        async def _run_async_iterable(self, gen: AsyncIterator[str]) -> None:
            buf: List[str] = []
            try:
                async for chunk in gen:
                    buf.append(chunk)
                    self._append(chunk)
            except Exception as exc:
                self._append(f"[{self._style('error')}]Error:[/{self._style('error')}] {exc}")
            if buf:
                self._append("".join(buf))

        def run(self) -> int:
            self._app.run()
            return 0

else:
    class Tui(_TuiBase):
        """Fallback plain-stdin TUI when prompt_toolkit is unavailable."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def _handle_text(self, text: str) -> None:
            if not text:
                return
            self._append(f"You: {text}")

            if text.startswith("/"):
                handled = self._handle_slash(text)
                if handled == "__EXIT__":
                    return
                if handled is not None:
                    self._append(handled)
                    return

            if self._on_user_message:
                try:
                    result = self._on_user_message(text)
                except Exception as exc:
                    self._append(f"Error: {exc}")
                    return
                if asyncio.iscoroutine(result):
                    result = asyncio.run(result)
                elif hasattr(result, "__aiter__"):
                    buf = []
                    async def _collect():
                        try:
                            async for c in result:
                                buf.append(c)
                        except Exception as exc:
                            self._append(f"Error: {exc}")
                    asyncio.run(_collect())
                    result = "".join(buf)
                if isinstance(result, str):
                    self._append(result)
                elif isinstance(result, dict) and "final_response" in result:
                    self._append(result["final_response"])
                else:
                    self._append(str(result))

        def run(self) -> int:
            try:
                while True:
                    try:
                        text = input("❯ ").strip()
                    except EOFError:
                        return 0
                    if not text:
                        continue
                    self._handle_text(text)
                    for line in self._output:
                        print(line)
                    self._output.clear()
            except KeyboardInterrupt:
                return 0
