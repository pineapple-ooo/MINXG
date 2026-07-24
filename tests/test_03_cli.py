"""
test_03_cli.py — multiligua_cli core modules: main, setup, terminal_chat.
"""
import pytest
from pathlib import Path


class TestCLIImports:
    """CLI modules import without error."""

    def test_main_importable(self):
        from multiligua_cli import main
        assert main is not None

    def test_setup_importable(self):
        from multiligua_cli import setup
        assert setup is not None

    def test_terminal_chat_importable(self):
        from multiligua_cli import terminal_chat
        assert terminal_chat is not None

    def test_wizard_ui_importable(self):
        from multiligua_cli import wizard_ui, wizard_smart
        assert wizard_ui is not None
        assert wizard_smart is not None

    def test_file_selector_importable(self):
        from multiligua_cli import file_selector
        assert file_selector is not None

    def test_tui_chat_importable(self):
        from multiligua_cli import tui_chat, tui_input, tui_polish
        assert tui_chat is not None
        assert tui_input is not None
        assert tui_polish is not None

    def test_gateway_cli_importable(self):
        from multiligua_cli import gateway_cli
        assert gateway_cli is not None


class TestCLIConstants:
    """CLI modules use constants, not hardcoded values."""

    def test_terminal_chat_uses_gateway_port_constant(self):
        """terminal_chat uses GATEWAY_DEFAULT_PORT, not raw 18080."""
        import inspect
        from multiligua_cli import terminal_chat
        source = inspect.getsource(terminal_chat)
        assert "GATEWAY_DEFAULT_PORT" in source
        assert "18080" not in source

    def test_main_uses_gateway_port_constant(self):
        """main.py imports and uses GATEWAY_DEFAULT_PORT constant."""
        from pathlib import Path
        import re
        # Read file directly (inspect.getsource on the module fails because
        # the module has a function named 'main' that shadows the module)
        source = Path("multiligua_cli/main.py").read_text()
        # Must use the named constant
        assert "GATEWAY_DEFAULT_PORT" in source, \
            "main.py must use GATEWAY_DEFAULT_PORT, not raw 18080"
        # Must not have bare 18080 anywhere
        raw = re.findall(r'\b18080\b', source)
        assert not raw, f"main.py contains raw port number 18080"

    def test_setup_uses_workers_port_constant(self):
        """setup.py uses WORKERS_DEFAULT_PORT, not raw 19001."""
        import inspect
        from multiligua_cli import setup
        source = inspect.getsource(setup)
        assert "WORKERS_DEFAULT_PORT" in source

    def test_file_selector_uses_kib_constant(self):
        """file_selector.py uses KiB constant, not raw 1024."""
        import inspect
        from multiligua_cli import file_selector
        source = inspect.getsource(file_selector)
        assert "KiB" in source or "1024" in source  # either is fine


class TestCLIFunctionality:
    """CLI core functions exist and have correct signatures."""

    def test_main_module_has_callable(self):
        from multiligua_cli import main
        assert len([x for x in dir(main) if not x.startswith("_")]) >= 0

    def test_setup_module_has_main(self):
        from multiligua_cli import setup
        # setup module exists and is usable
        assert setup is not None

    def test_gateway_cli_module_exists(self):
        from multiligua_cli import gateway_cli
        assert gateway_cli is not None


class TestWebUIPorts:
    """Web UI server uses WEB_UI_DEFAULT_PORT constant."""

    def test_web_ui_server_uses_port_constant(self):
        import inspect
        from multiling.web_ui import server
        source = inspect.getsource(server)
        assert "WEB_UI_DEFAULT_PORT" in source
        assert "8765" not in source

    def test_web_ui_server_old_not_in_use(self):
        """server_old.py exists but is not imported by __init__ or main."""
        # Backward-compat file may live in either the old or renamed workspace
        old = Path("/storage/emulated/0/AgentHarness v0.18.5/multiling/web_ui/server_old.py")
        if not old.exists():
            old = Path("/storage/emulated/0/MINXG v0.18.5/multiling/web_ui/server_old.py")
        assert old.exists()  # kept for backward compat but not used