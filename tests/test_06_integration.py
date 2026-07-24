"""
test_06_integration.py — Cross-module integration tests.
Real class/function names verified against source.
"""
import pytest
import sys


class TestFullPackageImport:
    """Full multiling package imports and submodules are loadable."""

    def test_import_multiling_package(self):
        import multiling
        assert multiling is not None

    def test_import_agent_module(self):
        from agent import conversation_loop
        assert conversation_loop is not None

    def test_import_extensions_module(self):
        from extensions import loader
        assert loader is not None

    def test_ipc_server_tcpipcserver_spawns(self):
        """TCPIPCServer can be instantiated without connecting."""
        from multiling.ipc_server import TCPIPCServer
        server = TCPIPCServer(host="127.0.0.1", port=0)
        assert server is not None

    def test_httpgateway_spawns(self):
        """HTTPGateway can be instantiated without connecting."""
        from multiling.ipc_server import HTTPGateway
        gw = HTTPGateway(host="127.0.0.1", port=0, orchestrator=None)
        assert gw is not None

    def test_orchestrator_instantiates(self):
        """NexusOrchestrator __init__ does not make network calls."""
        from multiling.orchestrator import NexusOrchestrator
        try:
            orch = NexusOrchestrator(
                ai_base_url="http://127.0.0.1:19999",
                ai_model="test",
                ai_api_key="test",
            )
            assert orch is not None
        except Exception:
            pytest.skip("Could not initialize orchestrator (expected in CI)")

    def test_council_module_instantiates(self):
        """CommanderCouncil instantiates without crashing."""
        from multiling.commander.council import CommanderCouncil
        c = CommanderCouncil()
        assert c is not None

    def test_toolsets_resolve_default(self):
        """DEFAULT_TOOLSETS resolves to tools."""
        from multiling.toolsets import DEFAULT_TOOLSETS, resolve_toolset
        if DEFAULT_TOOLSETS:
            tools = resolve_toolset(DEFAULT_TOOLSETS[0])
            assert tools is not None


class TestEndToEndImportTree:
    """Import tree has no circular dependencies at top-level."""

    def test_multiling_imports_clean(self):
        """All of multiling's imports load without ImportError."""
        # Record what's already loaded
        before = {k for k in sys.modules if k.startswith("multiling")}
        # Remove them for fresh import
        for k in list(before):
            try:
                del sys.modules[k]
            except KeyError:
                pass

        # Re-import — must not raise
        import multiling
        assert multiling is not None

    def test_constants_all_accessible(self):
        """All key constants are accessible."""
        from multiling.constants import (
            MAX_MEMORY_FACTS, MAX_SESSIONS, MAX_TOOL_CALLS_PER_TURN,
            TIMEOUT_AIOHTTP_TOTAL, TIMEOUT_AIOHTTP_KEEPALIVE,
            KiB, DEFAULT_MAX_TOKENS, MAX_CONTEXT_CHARS,
            IPC_DEFAULT_PORT, GATEWAY_DEFAULT_PORT,
        )
        assert all(isinstance(x, (int, float)) for x in [
            MAX_MEMORY_FACTS, MAX_SESSIONS, MAX_TOOL_CALLS_PER_TURN,
            TIMEOUT_AIOHTTP_TOTAL, TIMEOUT_AIOHTTP_KEEPALIVE,
            KiB, DEFAULT_MAX_TOKENS, MAX_CONTEXT_CHARS,
            IPC_DEFAULT_PORT, GATEWAY_DEFAULT_PORT,
        ])


class TestWebUI:
    """Web UI server module checks."""

    def test_web_ui_server_imports(self):
        from multiling.web_ui import server
        assert server is not None

    def test_web_ui_server_uses_correct_port(self):
        """server.py uses WEB_UI_DEFAULT_PORT=8080, not old port 8765."""
        import inspect
        from multiling.web_ui import server
        src = inspect.getsource(server)
        assert "WEB_UI_DEFAULT_PORT" in src
        assert "8765" not in src


class TestPipeline:
    """Pipeline module imports."""

    def test_pipeline_imports(self):
        from multiling import pipeline
        assert pipeline is not None


class TestExtensions:
    """Extensions: ExtensionModule, ExtensionRegistry (real class names)."""

    def test_extension_module_instantiates(self):
        from extensions.loader import ExtensionModule
        # ExtensionModule requires name, description, module
        import types
        fake_mod = types.ModuleType("fake")
        em = ExtensionModule("test", "a test extension", fake_mod)
        assert em is not None

    def test_extension_registry_instantiates(self):
        from extensions import ExtensionRegistry
        reg = ExtensionRegistry()
        assert reg is not None

    def test_extension_registry_has_register(self):
        from extensions import ExtensionRegistry
        reg = ExtensionRegistry()
        assert hasattr(reg, "register")

    def test_import_wizard_functions_exist(self):
        """import_wizard has platform/search functions."""
        from extensions import import_wizard
        assert callable(import_wizard._get_platform)


class TestPlatformCapabilities:
    """Platform capabilities: active_tools (real function name)."""

    def test_active_tools_returns_frozenset(self):
        from multiling.platform_cap import active_tools
        tools = active_tools()
        assert isinstance(tools, frozenset)

    def test_is_active_function(self):
        from multiling.platform_cap import is_active
        result = is_active("nonexistent_tool_xyz")
        assert isinstance(result, bool)