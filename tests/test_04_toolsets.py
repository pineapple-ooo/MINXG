"""
test_04_toolsets.py — toolsets and model_tools integrity.
Actual function names verified against source.
"""
import pytest


class TestToolsets:
    """Toolset registry — real functions from toolsets.py."""

    def test_default_toolsets_is_list(self):
        from multiling.toolsets import DEFAULT_TOOLSETS
        assert isinstance(DEFAULT_TOOLSETS, list)

    def test_toolsets_dict_has_keys(self):
        from multiling.toolsets import TOOLSETS
        assert isinstance(TOOLSETS, dict)
        assert len(TOOLSETS) > 0

    def test_toolset_aliases_mapping(self):
        from multiling.toolsets import TOOLSETS
        for name, spec in TOOLSETS.items():
            assert "tools" in spec or "aliases" in spec, f"{name} has no tools/aliases"

    def test_get_all_toolsets_returns_list(self):
        from multiling.toolsets import get_all_toolsets
        toolsets = get_all_toolsets()
        assert isinstance(toolsets, list), f"expected list, got {type(toolsets).__name__}"

    def test_get_toolset_tools(self):
        from multiling.toolsets import get_toolset_tools
        tools = get_toolset_tools("file")
        assert isinstance(tools, list)

    def test_tool_definitions_callable(self):
        from multiling.model_tools import get_tool_definitions
        defs = get_tool_definitions()
        assert isinstance(defs, list)


class TestModelTools:
    """model_tools.py functions (check_toolset_requirements, resolve_toolset, etc.)."""

    def test_check_toolset_requirements_exists(self):
        from multiling.model_tools import check_toolset_requirements
        req = check_toolset_requirements()
        assert isinstance(req, dict)

    def test_resolve_toolset_returns_tool_names(self):
        from multiling.model_tools import resolve_toolset
        # resolve_toolset returns the tool name string, not a list
        result = resolve_toolset("file")
        assert result is not None

    def test_get_available_toolsets_exists(self):
        from multiling.model_tools import get_available_toolsets
        avail = get_available_toolsets()
        assert isinstance(avail, dict)
        assert len(avail) > 0

    def test_get_toolset_for_tool(self):
        from multiling.model_tools import get_toolset_for_tool
        ts = get_toolset_for_tool("file")
        assert ts is not None

    def test_handle_function_call_exists(self):
        from multiling.model_tools import handle_function_call
        assert callable(handle_function_call)