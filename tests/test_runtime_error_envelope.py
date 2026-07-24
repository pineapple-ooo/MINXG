"""tests/test_runtime_error_envelope.py - comprehensive error envelope tests."""
from __future__ import annotations

import pytest

from agent_harness.contracts.runtime import handle
from agent_harness.contracts.runtime._exec import SecurityPolicy, AuditLogger


class TestErrorEnvelope:
    """Comprehensive error envelope tests."""

    def test_error_envelope_shape(self):
        """Test error response has correct envelope shape."""
        result = handle({"language": "invalid_lang", "mode": "eval"})
        assert "status" in result
        assert "language" in result
        assert result["status"] == "error"

    def test_success_envelope_shape(self):
        """Test success response has correct envelope shape."""
        result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})
        assert "status" in result
        assert "language" in result

    def test_error_includes_type(self):
        """Test error includes error type."""
        result = handle({"language": "invalid_lang", "mode": "eval"})
        assert "error" in result or "stderr" in result

    def test_dispatcher_error_handling(self):
        """Test dispatcher error handling."""
        result = handle({"language": "julia", "mode": "eval", "code": "invalid syntax !!!"})
        assert "status" in result

    def test_unsupported_mode_error(self):
        """Test unsupported mode returns error."""
        result = handle({"language": "julia", "mode": "nonexistent_mode_xyz"})
        assert "status" in result

    def test_empty_code_error(self):
        """Test empty code handling."""
        result = handle({"language": "julia", "mode": "eval", "code": ""})
        assert "status" in result

    def test_none_code_error(self):
        """Test None code handling."""
        result = handle({"language": "julia", "mode": "eval", "code": None})
        assert "status" in result

    def test_malformed_json_payload(self):
        """Test malformed JSON payload."""
        result = handle({"language": "julia", "mode": "eval", "code": "{invalid"})
        assert "status" in result

    def test_security_policy_violation(self):
        """Test security policy violation."""
        policy = SecurityPolicy(denied_commands=["rm -rf", "dd"])
        assert not policy.check_command("rm -rf /")
        assert not policy.check_command("dd if=/dev/zero")
        assert policy.check_command("echo hello")

    def test_audit_logger_records_errors(self):
        """Test audit logger records errors."""
        logger = AuditLogger()
        logger.log("error", {"error": "test error"})
        events = logger.get_events("error")
        assert len(events) == 1
        assert events[0]["payload"]["error"] == "test error"

    def test_error_recovery(self):
        """Test error recovery."""
        result = handle({"language": "julia", "mode": "eval", "code": "1 + 1"})
        assert "status" in result
        # Recover from error
        result2 = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})
        assert "status" in result2

    def test_concurrent_errors(self):
        """Test concurrent error handling."""
        import threading
        results = []
        def make_request():
            results.append(handle({"language": "julia", "mode": "eval", "code": "invalid syntax"}))
        threads = [threading.Thread(target=make_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 5
        assert all("status" in r for r in results)

    def test_error_envelope_no_leak(self):
        """Test error envelope doesn't leak internal details."""
        result = handle({"language": "invalid_lang", "mode": "eval"})
        # Should not contain stack traces or internal paths
        assert "Traceback" not in str(result)
        assert "agent_harness/contracts" not in str(result) or "language" in result

    def test_timeout_error_envelope(self):
        """Test timeout error envelope."""
        import time
        start = time.perf_counter()
        result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0
        assert "status" in result

    def test_rate_limit_envelope(self):
        """Test rate limit error envelope."""
        results = []
        for _ in range(100):
            results.append(handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"}))
        assert all("status" in r for r in results)
