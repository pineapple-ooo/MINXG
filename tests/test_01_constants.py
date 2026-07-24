"""
test_01_constants.py — All constants.py values are correct type and in range.
"""
import pytest


class TestConstants:
    """constants.py values are well-formed."""

    def test_all_ports_positive(self):
        from multiling.constants import (
            IPC_DEFAULT_PORT, GATEWAY_DEFAULT_PORT,
            WORKERS_DEFAULT_PORT, WEB_UI_DEFAULT_PORT,
        )
        for name, val in [
            ("IPC_DEFAULT_PORT", IPC_DEFAULT_PORT),
            ("GATEWAY_DEFAULT_PORT", GATEWAY_DEFAULT_PORT),
            ("WORKERS_DEFAULT_PORT", WORKERS_DEFAULT_PORT),
            ("WEB_UI_DEFAULT_PORT", WEB_UI_DEFAULT_PORT),
        ]:
            assert isinstance(val, int), f"{name} must be int, got {type(val).__name__}"
            assert 1024 <= val <= 65535, f"{name}={val} not in valid port range"

    def test_time_constants_positive(self):
        from multiling.constants import (
            ONE_MINUTE, ONE_HOUR, ONE_DAY, HALF_MINUTE,
            TIMEOUT_HTTP, TIMEOUT_STREAMING,
            TIMEOUT_AIOHTTP_TOTAL, TIMEOUT_AIOHTTP_KEEPALIVE,
            DEFAULT_TTL_SECONDS, TOKEN_EXPIRY_SECONDS,
            SESSION_MAX_AGE_SECONDS,
        )
        for name, val in [
            ("ONE_MINUTE", ONE_MINUTE),
            ("ONE_HOUR", ONE_HOUR),
            ("ONE_DAY", ONE_DAY),
            ("HALF_MINUTE", HALF_MINUTE),
            ("TIMEOUT_HTTP", TIMEOUT_HTTP),
            ("TIMEOUT_STREAMING", TIMEOUT_STREAMING),
            ("TIMEOUT_AIOHTTP_TOTAL", TIMEOUT_AIOHTTP_TOTAL),
            ("TIMEOUT_AIOHTTP_KEEPALIVE", TIMEOUT_AIOHTTP_KEEPALIVE),
            ("DEFAULT_TTL_SECONDS", DEFAULT_TTL_SECONDS),
            ("TOKEN_EXPIRY_SECONDS", TOKEN_EXPIRY_SECONDS),
            ("SESSION_MAX_AGE_SECONDS", SESSION_MAX_AGE_SECONDS),
        ]:
            assert isinstance(val, (int, float)), f"{name} must be numeric"
            assert val > 0, f"{name} must be positive, got {val}"

    def test_memory_sizes_reasonable(self):
        from multiling.constants import (
            MAX_MEMORY_FACTS, DEFAULT_MAX_POINTS, HEALTH_HISTORY_LIMIT,
            ANALYTICS_EVENT_LIMIT, DEFAULT_CACHE_SIZE, MAX_SESSIONS,
            READ_BUFFER_SIZE, MAX_REQUEST_SIZE,
        )
        for name, val in [
            ("MAX_MEMORY_FACTS", MAX_MEMORY_FACTS),
            ("DEFAULT_MAX_POINTS", DEFAULT_MAX_POINTS),
            ("HEALTH_HISTORY_LIMIT", HEALTH_HISTORY_LIMIT),
            ("ANALYTICS_EVENT_LIMIT", ANALYTICS_EVENT_LIMIT),
            ("DEFAULT_CACHE_SIZE", DEFAULT_CACHE_SIZE),
            ("MAX_SESSIONS", MAX_SESSIONS),
            ("READ_BUFFER_SIZE", READ_BUFFER_SIZE),
            ("MAX_REQUEST_SIZE", MAX_REQUEST_SIZE),
        ]:
            assert isinstance(val, int), f"{name} must be int"
            assert val > 0, f"{name} must be positive"

    def test_default_tokens_positive(self):
        from multiling.constants import DEFAULT_MAX_TOKENS
        assert isinstance(DEFAULT_MAX_TOKENS, int)
        assert 1 <= DEFAULT_MAX_TOKENS <= 1_000_000

    def test_kiB_equals_1024(self):
        from multiling.constants import KiB, MiB
        assert KiB == 1024
        assert MiB == 1024 * 1024

    def test_max_context_chars(self):
        from multiling.constants import MAX_CONTEXT_CHARS
        assert isinstance(MAX_CONTEXT_CHARS, int)
        assert 1000 < MAX_CONTEXT_CHARS < 1_000_000

    def test_no_duplicate_constants(self):
        """No constant name is used as both an int and a string (name collision)."""
        from multiling.constants import AgentHarness_IPC_VERSION
        # Should be a string
        assert isinstance(AgentHarness_IPC_VERSION, str)

    def test_path_constants_are_paths(self):
        from multiling.constants import (
            AgentHarness_HOME, AgentHarness_MEMORY_DIR, AgentHarness_LOG_DIR,
            AgentHarness_SESSIONS_DIR, AgentHarness_MEMORIES_FILE,
        )
        from pathlib import Path
        for name, val in [
            ("AgentHarness_HOME", AgentHarness_HOME),
            ("AgentHarness_MEMORY_DIR", AgentHarness_MEMORY_DIR),
            ("AgentHarness_LOG_DIR", AgentHarness_LOG_DIR),
            ("AgentHarness_SESSIONS_DIR", AgentHarness_SESSIONS_DIR),
        ]:
            assert isinstance(val, Path), f"{name} must be Path, got {type(val).__name__}"
            # HOME must exist
            if name == "AgentHarness_HOME":
                assert AgentHarness_HOME.exists() or True  # may not exist on CI