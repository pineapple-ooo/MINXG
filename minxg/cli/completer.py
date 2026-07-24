"""Slash-command and tool-name completer for the prompt_toolkit TUI."""

from __future__ import annotations

from typing import Iterable, Optional

try:
    from prompt_toolkit.completion import CompleteEvent, Completer, Completion

    HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_PROMPT_TOOLKIT = False
    Completer = object


if HAS_PROMPT_TOOLKIT:
    class SlashCommandCompleter(Completer):
        """Complete slash commands and optionally tool names."""

        def __init__(self, tool_names: Optional[Iterable[str]] = None) -> None:
            self._tool_names = sorted(set(tool_names or []))

        def get_completions(
            self, document, complete_event: CompleteEvent
        ) -> Iterable[Completion]:
            text = document.text_before_cursor
            if not text.startswith("/"):
                return

            query = text[1:].lower()
            for cmd in (
                "/help",
                "/model",
                "/tools",
                "/clear",
                "/status",
                "/theme",
                "/exit",
            ):
                if cmd[1:].lower().startswith(query):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                    )
                    return

            if text.startswith("/tool "):
                prefix = text[6:].lower()
                for tool in self._tool_names:
                    if tool.lower().startswith(prefix):
                        yield Completion(
                            tool,
                            start_position=-len(text),
                            display=tool,
                        )
else:
    class SlashCommandCompleter:  # type: ignore[no-redef]
        """No-op completer when prompt_toolkit is unavailable."""

        def __init__(self, tool_names: Optional[Iterable[str]] = None) -> None:
            pass
