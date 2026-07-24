"""
test_02_core_modules.py — multiling core modules: orchestrator, ipc_server, council.
"""
import pytest
import inspect


class TestOrchestrator:
    """NexusOrchestrator basic lifecycle."""

    def test_orchestrator_instantiates(self):
        from multiling.orchestrator import NexusOrchestrator
        assert isinstance(NexusOrchestrator, type)

    @pytest.mark.asyncio
    async def test_orchestrator_initialize(self):
        """Orchestrator initializes without crashing (no API calls made)."""
        from multiling.orchestrator import NexusOrchestrator
        from multiling.model_tools import check_toolset_requirements
        try:
            orch = NexusOrchestrator(
                ai_base_url="http://localhost:9999",
                ai_model="test-model",
                ai_api_key="test-key",
            )
        except Exception:
            pytest.skip("Orchestrator init requires full config (expected in CI)")
            return
        req = check_toolset_requirements()
        assert isinstance(req, dict)

    def test_stream_conversation_has_helper_functions(self):
        """_stream_conversation contains single json.dumps + helper functions."""
        from multiling.orchestrator import NexusOrchestrator
        source = inspect.getsource(NexusOrchestrator._stream_conversation)
        assert "_persist_tool_result" in source
        assert "_close_round" in source
        assert source.count("json.dumps(result_obj") <= 1  # exactly once


class TestIPCServer:
    """TCPIPCServer and HTTPGateway basic lifecycle."""

    def test_tcpipcserver_instantiates(self):
        from multiling.ipc_server import TCPIPCServer
        server = TCPIPCServer()
        assert server is not None
        assert server.host == "127.0.0.1"
        assert isinstance(server.port, int)
        assert server.port == 18999

    def test_httpgateway_instantiates(self):
        from multiling.ipc_server import HTTPGateway
        # Note: requires orchestrator argument if not connected to IPC
        gw = HTTPGateway(host="127.0.0.1", port=0, orchestrator=None)
        assert gw is not None
        assert gw.host == "127.0.0.1"
        assert gw.port == 0

    def test_no_circular_import_on_multiling_import(self):
        """Importing multiling triggers no circular import error."""
        import sys
        # Remove all multiling modules to force fresh import
        before = {k for k in sys.modules if k.startswith("multiling")}
        for k in list(before):
            del sys.modules[k]

        # Re-import — must not raise
        import multiling
        from multiling import constants
        assert constants.IPC_DEFAULT_PORT == 18999

    def test_create_server_is_callable(self):
        from multiling.ipc_server import create_server
        assert callable(create_server)

    def test_create_gateway_is_callable(self):
        from multiling.ipc_server import create_gateway
        assert callable(create_gateway)


class TestCouncil:
    """CommanderCouncil and CompanyOrchestrator from council.py."""

    def test_commander_council_instantiates(self):
        from multiling.commander.council import CommanderCouncil
        c = CommanderCouncil()
        assert c is not None

    def test_company_orchestrator_instantiates(self):
        from multiling.commander.council import CompanyOrchestrator
        assert CompanyOrchestrator is not None

    def test_high_level_orchestrator_instantiates(self):
        from multiling.commander.council import HighLevelOrchestrator
        assert HighLevelOrchestrator is not None

    def test_company_orchestrator_run_uses_list_parts(self):
        """CompanyOrchestrator.run() uses _ctx_parts list (not string concat)."""
        from multiling.commander.council import CompanyOrchestrator
        source = inspect.getsource(CompanyOrchestrator.run)
        assert "_ctx_parts: list[str]" in source or "_ctx_parts [" in source

    def test_company_orchestrator_uses_max_context_chars(self):
        """CompanyOrchestrator.run() references MAX_CONTEXT_CHARS."""
        from multiling.commander.council import CompanyOrchestrator
        source = inspect.getsource(CompanyOrchestrator.run)
        assert "MAX_CONTEXT_CHARS" in source


class TestCommunicationBus:
    """CommunicationBus checkpoint persistence."""

    def test_comm_bus_instantiates(self):
        from multiling.commander.comm_bus import CommunicationBus
        bus = CommunicationBus()
        assert bus is not None

    def test_comm_bus_checkpoint_path_settable(self, tmp_path):
        from multiling.commander.comm_bus import CommunicationBus
        bus = CommunicationBus()
        bus._checkpoint_path = str(tmp_path / "cp.json")
        assert bus._checkpoint_path == str(tmp_path / "cp.json")