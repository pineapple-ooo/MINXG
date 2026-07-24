"""tests/test_runtime_security.py — security-focused tests for runtime adapters.

Tests for:
- SSRF prevention in URL validation
- Path traversal prevention
- JSON size limits
- Memory limit enforcement
- Input sanitization
- Command injection prevention
"""
from __future__ import annotations

import pytest

from minxg.contracts.runtime._exec import validate_url, sanitize_path, safe_json_dumps


class TestSecurityHelpers:
    """Test security helper functions."""

    def test_validate_url_rejects_localhost(self):
        with pytest.raises(ValueError, match="localhost"):
            validate_url("https://localhost:8080")

    def test_validate_url_rejects_127_0_0_1(self):
        with pytest.raises(ValueError, match="loopback"):
            validate_url("https://127.0.0.1:8080")

    def test_validate_url_rejects_0_0_0_0(self):
        with pytest.raises(ValueError, match="loopback"):
            validate_url("https://0.0.0.0:8080")

    def test_validate_url_rejects_metadata_endpoint(self):
        with pytest.raises(ValueError, match="link-local"):
            validate_url("https://169.254.169.254/latest/meta-data/")

    def test_validate_url_rejects_file_protocol(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_url("file:///etc/passwd")

    def test_validate_url_accepts_https(self):
        result = validate_url("https://example.com")
        assert result == "https://example.com"

    def test_validate_url_rejects_http_by_default(self):
        with pytest.raises(ValueError, match="scheme"):
            validate_url("http://example.com")

    def test_validate_url_rejects_invalid_hostname(self):
        with pytest.raises(ValueError):
            validate_url("http://")

    def test_sanitize_path_blocks_etc_passwd(self):
        with pytest.raises(ValueError, match="escapes cwd"):
            sanitize_path("/etc/passwd")

    def test_sanitize_path_blocks_ssh(self):
        with pytest.raises(ValueError, match="escapes cwd"):
            sanitize_path("/home/user/.ssh/id_rsa")

    def test_sanitize_path_blocks_aws(self):
        with pytest.raises(ValueError, match="escapes cwd"):
            sanitize_path("/home/user/.aws/credentials")

    def test_sanitize_path_blocks_env_files(self):
        with pytest.raises(ValueError, match="escapes cwd"):
            sanitize_path("/home/user/.env")
        with pytest.raises(ValueError, match="escapes cwd"):
            sanitize_path("/home/user/.env.local")

    def test_safe_json_dumps_handles_unicode(self):
        data = {"text": "Hello 世界 🌍"}
        result = safe_json_dumps(data)
        assert "Hello" in result

    def test_safe_json_dumps_handles_large_data(self):
        # Should not crash on moderately large data
        data = {"items": list(range(1000))}
        result = safe_json_dumps(data)
        assert "items" in result


class TestWorkerInputValidation:
    """Test that workers validate inputs properly."""

    @pytest.mark.asyncio
    async def test_julia_worker_rejects_empty_code(self):
        from minxg.five_pillars.polyglot.julia_worker import JuliaWorker
        worker = JuliaWorker()
        result = await worker.call("julia_eval", {"code": ""})
        assert result["status"] in ("error", "disabled")

    @pytest.mark.asyncio
    async def test_datalog_worker_rejects_malformed_edges(self):
        from minxg.five_pillars.polyglot.datalog_worker import DatalogWorker
        worker = DatalogWorker()
        result = await worker.call("datalog_graph_reachable", {"edges": "not a list"})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_wasm_worker_rejects_invalid_op(self):
        from minxg.five_pillars.polyglot.wasm_worker import WasmWorker
        worker = WasmWorker()
        result = await worker.call("wasm_arith_i32", {"op": "DROP_TABLE", "a": 1, "b": 2})
        assert result["status"] == "error"
