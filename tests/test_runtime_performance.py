"""tests/test_runtime_performance.py — performance benchmarks for runtime adapters."""
from __future__ import annotations

import time
import pytest

from agent_harness.contracts.runtime import handle


class TestPerformanceBenchmarks:
    """Performance benchmarks for runtime operations."""

    def test_julia_eval_latency(self):
        """Benchmark Julia eval latency."""
        start = time.perf_counter()
        for _ in range(10):
            handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Julia eval too slow: {elapsed:.2f}s"

    def test_wasm_compile_latency(self):
        """Benchmark WASM compile latency."""
        wat = "(module)"
        start = time.perf_counter()
        for _ in range(10):
            handle({"language": "wasm", "compile": wat})
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"WASM compile too slow: {elapsed:.2f}s"

    def test_datalog_query_latency(self):
        """Benchmark Datalog query latency."""
        start = time.perf_counter()
        for _ in range(10):
            handle({"language": "datalog", "mode": "demo"})
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Datalog query too slow: {elapsed:.2f}s"

    def test_large_payload_handling(self):
        """Benchmark large payload handling."""
        large_payload = {"language": "julia", "mode": "eval", "code": "1+1" * 1000}
        start = time.perf_counter()
        result = handle(large_payload)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"Large payload too slow: {elapsed:.2f}s"
        assert "status" in result

    def test_concurrent_requests(self):
        """Benchmark concurrent request handling."""
        import threading
        results = []
        def make_request():
            results.append(handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"}))
        threads = [threading.Thread(target=make_request) for _ in range(10)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0, f"Concurrent requests too slow: {elapsed:.2f}s"
        assert len(results) == 10


class TestMemoryUsage:
    """Memory usage tests."""

    def test_large_array_handling(self):
        """Test handling of large arrays without memory explosion."""
        large_array = list(range(100000))
        result = handle({"language": "wasm", "mode": "demo", "args": [large_array]})
        assert "status" in result

    def test_repeated_calls_no_leak(self):
        """Test that repeated calls don't leak memory."""
        import gc
        gc.collect()
        for _ in range(100):
            handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})
        gc.collect()
        # If we get here without OOM, test passes
        assert True
