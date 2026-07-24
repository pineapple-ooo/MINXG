"""tests/test_runtime_security_advanced.py - advanced security tests."""
from __future__ import annotations

import pytest

from minxg.contracts.runtime import handle
from minxg.contracts.runtime._exec import SecurityPolicy, sanitize_env, validate_file_path, AuditLogger


class TestSecurityPolicy:
    """Test security policy enforcement."""

    def test_default_policy_blocks_dangerous_commands(self):
        """Test default policy blocks dangerous commands."""
        policy = SecurityPolicy()
        assert not policy.check_command("rm -rf /")
        assert not policy.check_command("dd if=/dev/zero")
        assert not policy.check_command("shutdown -h now")
        assert policy.check_command("echo hello")

    def test_url_validation(self):
        """Test URL validation."""
        policy = SecurityPolicy(allowed_hosts=["localhost", "127.0.0.1"])
        assert policy.check_url("http://localhost/api")
        assert policy.check_url("http://127.0.0.1:8080")
        assert not policy.check_url("http://evil.com/steal")
        assert not policy.check_url("not-a-url")

    def test_memory_limits(self):
        """Test memory limits."""
        policy = SecurityPolicy(max_memory_mb=100)
        assert policy.check_memory(50)
        assert not policy.check_memory(150)

    def test_cpu_limits(self):
        """Test CPU time limits."""
        policy = SecurityPolicy(max_cpu_seconds=5.0)
        assert policy.check_cpu(3.0)
        assert not policy.check_cpu(10.0)


class TestEnvironmentSanitization:
    """Test environment variable sanitization."""

    def test_removes_dangerous_vars(self):
        """Test removal of dangerous environment variables."""
        env = {"LD_PRELOAD": "/evil.so", "HOME": "/home", "SAFE": "1"}
        clean = sanitize_env(env)
        assert "LD_PRELOAD" not in clean
        assert "HOME" not in clean
        assert "SAFE" in clean

    def test_removes_path_vars(self):
        """Test removal of PATH-related variables."""
        env = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/usr/lib"}
        clean = sanitize_env(env)
        assert "PATH" not in clean
        assert "LD_LIBRARY_PATH" not in clean


class TestFilePathValidation:
    """Test file path validation."""

    def test_allows_path_in_allowed_dir(self):
        """Test path in allowed directory."""
        allowed = ["/tmp/allowed"]
        assert validate_file_path("/tmp/allowed/file.txt", allowed)

    def test_blocks_path_outside_allowed_dir(self):
        """Test path outside allowed directory."""
        allowed = ["/tmp/allowed"]
        assert not validate_file_path("/etc/passwd", allowed)
        assert not validate_file_path("/tmp/other/file.txt", allowed)


class TestAuditLogger:
    """Test audit logging."""

    def test_logs_events(self):
        """Test event logging."""
        logger = AuditLogger()
        logger.log("test_event", {"key": "value"})
        events = logger.get_events()
        assert len(events) == 1
        assert events[0]["type"] == "test_event"

    def test_filters_by_type(self):
        """Test filtering events by type."""
        logger = AuditLogger()
        logger.log("type_a", {})
        logger.log("type_b", {})
        events = logger.get_events("type_a")
        assert len(events) == 1
        assert events[0]["type"] == "type_a"


class TestSecurityIntegration:
    """Test security in integration scenarios."""

    def test_handle_respects_security_policy(self):
        """Test handle respects security policy."""
        result = handle({
            "language": "julia",
            "mode": "eval",
            "code": "sqrt(4.0)",
        })
        assert "status" in result

    def test_handle_rejects_invalid_language(self):
        """Test handle rejects invalid language."""
        result = handle({"language": "evil_lang", "mode": "eval"})
        assert result["status"] == "error"

    def test_handle_handles_malformed_payload(self):
        """Test handle handles malformed payload."""
        result = handle({"language": "julia", "mode": "eval", "code": None})
        assert "status" in result
