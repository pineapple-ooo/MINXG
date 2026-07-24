"""prompt_toolkit layout for the AgentHarness TUI."""

from __future__ import annotations

from typing import Any, Dict

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import (
        ConditionalContainer,
        Float,
        FloatContainer,
        HSplit,
        Layout,
        VSplit,
        Window,
        WindowAlign,
    )
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.widgets import TextArea

    HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_PROMPT_TOOLKIT = False


def build_layout(
    *,
    on_submit,
    completer,
    style,
    initial_status: Dict[str, Any],
) -> Layout:
    if not HAS_PROMPT_TOOLKIT:
        raise RuntimeError("prompt_toolkit is required for build_layout")

    kb = KeyBindings()

    input_field = TextArea(
        prompt="❯ ",
        multiline=False,
        wrap_lines=False,
        completer=completer,
        complete_while_typing=True,
        key_bindings=kb,
    )

    @kb.add("enter")
    def _enter(event: Any) -> None:
        text = input_field.text.strip()
        input_field.text = ""
        on_submit(text)

    @kb.add("c-c")
    def _ctrl_c(event: Any) -> None:
        input_field.text = ""
        on_submit("/exit")

    output_control = FormattedTextControl(focusable=False, text="")

    output_window = Window(
        content=output_control,
        wrap_lines=True,
        always_hide_cursor=True,
        height=Dimension(weight=1),
    )

    status_bar = Window(
        content=FormattedTextControl(
            text=lambda: " | ".join(
                f"{k}={v}" for k, v in initial_status.items()
            )
        ),
        height=1,
        style="class:status",
        align=WindowAlign.LEFT,
    )

    root = HSplit(
        [
            output_window,
            status_bar,
            input_field,
        ]
    )

    floats = [
        Float(
            ConditionalContainer(
                content=Window(content=FormattedTextControl(text="")),
                filter=False,
            )
        )
    ]

    container = FloatContainer(
        root,
        floats=floats,
    )

    return Layout(container)
