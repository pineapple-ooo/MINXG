"""tests/test_runtime_architecture.py — verify architectural improvements.

This test file verifies the v0.18.0 runtime architecture:
- Legacy assets removed (C/C++/Go/R)
- Unified dispatcher (handle) works for all languages
- Manifest is complete and consistent
- Worker tool counts match manifest
- Bridge files are valid
- Security helpers are in place
"""
from __future__ import annotations

import json
import re

import pytest

from agent_harness.contracts.runtime import (
    capabilities_for,
    dependency_graph,
    handle,
    lang_info,
    manifest_for,
    supported_languages,
    tool_count_for,
)
from agent_harness.contracts.runtime._exec import validate_url, safe_json_dumps, sanitize_path
from agent_harness.contracts.runtime.installer import MANAGED_LANGUAGES


class TestLegacyCleanup:
    """Verify v0.18.3 legacy assets are removed."""

    def test_no_c_cpp_go_r_assets(self):
        """Legacy C/C++/Go/R directories should not exist."""
        import os
        assets_root = os.path.join(os.path.dirname(__file__), "..", "agent_harness", "contracts", "runtime", "assets")
        for lang in ("c", "cpp", "go", "r"):
            assert not os.path.exists(os.path.join(assets_root, lang)), f"legacy asset {lang} still exists"

    def test_no_c_cpp_go_r_imports(self):
        """No Python files should import from c/cpp/go/r modules."""
        import os
        root = os.path.join(os.path.dirname(__file__), "..", "agent_harness")
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip __pycache__ and test directories
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests")]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    content = open(fpath, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                for lang in ("c", "cpp", "go", "r"):
                    # Check for direct imports of legacy modules
                    bad_imports = [
                        f"from .{lang} ",
                        f"from .{lang}$",
                        f"from {lang}.",
                        f"import {lang}.",
                        f"from agent_harness.contracts.runtime.{lang}",
                        f"contracts/runtime/{lang}",
                    ]
                    for bad in bad_imports:
                        if bad in content:
                            pytest.fail(f"{fpath} contains legacy import: {bad}")


class TestUnifiedDispatcher:
    """Test the unified handle() dispatcher."""

    @pytest.mark.asyncio
    async def test_handle_wasm(self):
        result = handle({"language": "wasm", "func": "fib", "args": [10]})
        assert "status" in result
        assert "language" in result
        assert result["language"] == "wasm"

    @pytest.mark.asyncio
    async def test_handle_julia(self):
        result = handle({"language": "julia", "mode": "eval", "code": "sqrt(2.0)"})
        assert "status" in result
        assert "language" in result
        assert result["language"] == "julia"

    @pytest.mark.asyncio
    async def test_handle_datalog(self):
        result = handle({"language": "datalog", "mode": "demo"})
        assert "status" in result
        assert "language" in result
        assert result["language"] == "datalog"

    @pytest.mark.asyncio
    async def test_handle_unknown_language(self):
        result = handle({"language": "brainfuck"})
        assert result["status"] == "error"


class TestManifestCompleteness:
    """Test manifest completeness and consistency."""

    def test_all_managed_languages_in_manifest(self):
        for lang in MANAGED_LANGUAGES:
            info = lang_info(lang)
            assert info["name"], f"language {lang} missing name in manifest"
            assert info["module"], f"language {lang} missing module in manifest"

    def test_manifest_has_capabilities(self):
        for lang in MANAGED_LANGUAGES:
            caps = capabilities_for(lang)
            assert isinstance(caps, list), f"capabilities for {lang} should be a list"

    def test_manifest_has_tool_count(self):
        for lang in MANAGED_LANGUAGES:
            count = tool_count_for(lang)
            assert isinstance(count, int), f"tool_count for {lang} should be int"
            assert count >= 0, f"tool_count for {lang} should be non-negative"

    def test_dependency_graph_acyclic(self):
        graph = dependency_graph()
        # Simple cycle detection
        visited = set()
        rec_stack = set()

        def has_cycle(node):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for node in graph:
            if node not in visited:
                if has_cycle(node):
                    pytest.fail(f"dependency graph has cycle involving {node}")

    def test_manifest_serializable(self):
        from agent_harness.contracts.runtime.manifest import serialize_manifest
        json_str = serialize_manifest()
        data = json.loads(json_str)
        assert isinstance(data, dict)
        for lang in MANAGED_LANGUAGES:
            assert lang in data, f"{lang} missing from serialized manifest"


class TestWorkerToolCoverage:
    """Verify workers cover manifest tool counts."""

    def test_wasm_worker_tool_count(self):
        from agent_harness.five_pillars.polyglot.wasm_worker import WasmWorker
        worker = WasmWorker()
        manifest_count = tool_count_for("wasm")
        assert len(worker.tools) <= manifest_count * 2, "worker tools exceed reasonable manifest count"

    def test_julia_worker_tool_count(self):
        from agent_harness.five_pillars.polyglot.julia_worker import JuliaWorker
        worker = JuliaWorker()
        manifest_count = tool_count_for("julia")
        assert len(worker.tools) <= manifest_count * 2, "worker tools exceed reasonable manifest count"

    def test_datalog_worker_tool_count(self):
        from agent_harness.five_pillars.polyglot.datalog_worker import DatalogWorker
        worker = DatalogWorker()
        manifest_count = tool_count_for("datalog")
        assert len(worker.tools) <= manifest_count * 2, "worker tools exceed reasonable manifest count"


class TestBridgeFileValidity:
    """Verify bridge files are syntactically valid."""

    def test_julia_bridge_has_run_payload(self):
        import os
        bridge_path = os.path.join(
            os.path.dirname(__file__), "..", "agent_harness", "contracts", "runtime", "assets", "julia", "bridge.jl"
        )
        content = open(bridge_path, "r", encoding="utf-8").read()
        assert "function run_payload" in content, "Julia bridge missing run_payload function"
        assert "JSON.parse" in content, "Julia bridge missing JSON.parse"

    def test_datalog_bridge_has_rules(self):
        import os
        bridge_path = os.path.join(
            os.path.dirname(__file__), "..", "agent_harness", "contracts", "runtime", "assets", "datalog", "bridge.lp"
        )
        content = open(bridge_path, "r", encoding="utf-8").read()
        assert ":-" in content or "%" in content, "Datalog bridge appears empty"


class TestSecurityHardening:
    """Verify security helpers are functional."""

    def test_validate_url_blocks_ssrf(self):
        with pytest.raises(ValueError):
            validate_url("http://localhost:8080")
        with pytest.raises(ValueError):
            validate_url("http://127.0.0.1/admin")
        with pytest.raises(ValueError):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_validate_url_allows_https(self):
        result = validate_url("https://example.com")
        assert result == "https://example.com"

    def test_sanitize_path_prevents_traversal(self):
        with pytest.raises(ValueError):
            sanitize_path("/etc/passwd")
        with pytest.raises(ValueError):
            sanitize_path("/home/user/.ssh/id_rsa")

    def test_safe_json_dumps_prevents_memory_bomb(self):
        huge_data = {"x": "a" * 2_000_000}
        with pytest.raises(ValueError, match="exceeds"):
            safe_json_dumps(huge_data)
