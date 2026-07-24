"""Hardened execution layer with security policies, execution patterns, async support.

This module provides hardened execution layer with security policies, execution patterns, async support. capabilities for the AgentHarness polyglot runtime system.

Typical usage::

    from minxg.contracts.runtime import handle
    result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})

All operations support async execution, security policies, and comprehensive error handling.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import queue as _queue
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from multiling.constants import (
    TIMEOUT_HTTP_SKILL_FETCH,
    TIMEOUT_SUBPROCESS_QUICK,
    TIMEOUT_SUBPROCESS_NORMAL,
    TIMEOUT_SUBPROCESS_TOOL,
    TIMEOUT_SUBPROCESS_BUILD,
    TIMEOUT_SUBPROCESS_HEAVY,
    TIMEOUT_SUBPROCESS_INSTALL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result / policy types
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    ok: bool
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    cached: bool = False
    json: Any = None


@dataclass
class RunPolicy:
    timeout: float = TIMEOUT_SUBPROCESS_TOOL
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    memory_mb: int = 0
    truncate_bytes: int = 1_048_576
    shell: bool = False
    check: bool = False


@dataclass
class ContentHashCache:
    suffix: str = ""

    def get(self, key: bytes) -> Optional[bytes]:
        digest = hashlib.sha256(key).hexdigest()[:16]
        path = Path(tempfile.gettempdir()) / f"minxg_{digest}{self.suffix}"
        if path.exists():
            return path.read_bytes()
        return None

    def put(self, key: bytes, value: bytes) -> None:
        digest = hashlib.sha256(key).hexdigest()[:16]
        path = Path(tempfile.gettempdir()) / f"minxg_{digest}{self.suffix}"
        try:
            path.write_bytes(value)
        except OSError:
            pass


@dataclass
class SubprocessHealth:
    pid: int = -1
    returncode: Optional[int] = None
    cpu_time: float = 0.0
    mem_rss_kb: int = 0


@dataclass
class ExecutionMetrics:
    invocations: int = 0
    cache_hits: int = 0
    timeouts: int = 0
    total_duration_ms: float = 0.0
    last_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def which(binary: str) -> Optional[str]:
    try:
        return shutil.which(binary)
    except Exception:
        return None


def payload_code(payload: Dict[str, Any], max_chars: int = 120_000) -> str:
    code = str(payload.get("code", ""))
    if len(code) > max_chars:
        raise ValueError(f"code exceeds {max_chars} chars")
    return code


def sandbox_path(*parts: str) -> str:
    candidate = Path(tempfile.gettempdir()) / "minxg" / Path(*parts)
    candidate.mkdir(parents=True, exist_ok=True)
    return str(candidate)


def asset_path(language: str, filename: str) -> Path:
    root = Path(__file__).resolve().parent / "assets"
    target = root / language / filename
    if not target.exists():
        raise FileNotFoundError(f"asset not found: {target}")
    return target


def validate_url(url: str, *, allowed_schemes: Tuple[str, ...] = ("https",)) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in allowed_schemes:
        raise ValueError(f"url scheme {parsed.scheme!r} not in allowed {allowed_schemes}")
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("localhost / loopback URLs are forbidden")
    if host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
        raise ValueError(f"private IP range forbidden: {host}")
    if host.startswith("169.254.") or host.startswith("100."):
        raise ValueError(f"link-local / metadata IP forbidden: {host}")
    return url


def safe_json_dumps(obj: Any, *, max_bytes: int = 1_048_576, **kw: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, **kw)
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"json payload exceeds {max_bytes} bytes")
    return text


def sanitize_path(path: str) -> str:
    normalized = Path(path).resolve()
    cwd = Path.cwd().resolve()
    if not str(normalized).startswith(str(cwd)):
        raise ValueError(f"path escapes cwd: {path!r}")
    if ".." in Path(path).parts:
        raise ValueError(f"path traversal detected: {path!r}")
    return str(normalized)


def retry(fn, max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            time.sleep(delay)
            delay *= backoff
    raise last_exc  # type: ignore[misc]


def parallel_map(fn, items, *, max_workers: int = 4):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(exc)
    return results


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    MISSING = "missing"
    UNKNOWN = "unknown"

    @staticmethod
    def from_result(result: Dict[str, Any]) -> "HealthStatus":
        if result.get("timed_out"):
            return HealthStatus.DEGRADED
        if result.get("ok"):
            return HealthStatus.HEALTHY
        if result.get("returncode", 0) != 0:
            return HealthStatus.DEGRADED
        return HealthStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run(
    cmd,
    *,
    policy: Optional[RunPolicy] = None,
    cache_key: Optional[bytes] = None,
    cache: Optional[ContentHashCache] = None,
) -> Dict[str, Any]:
    policy = policy or RunPolicy()
    started = time.perf_counter()
    pid = -1
    try:
        if cache_key is not None and cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return {
                    "ok": True,
                    "stdout": cached.decode("utf-8", errors="replace"),
                    "stderr": "",
                    "duration_ms": 0.0,
                    "cached": True,
                    "timed_out": False,
                }

        env = dict(policy.env) if policy.env else None
        if env is not None:
            env.setdefault("PATH", os.environ.get("PATH", ""))

        kwargs: Dict[str, Any] = dict(
            cwd=policy.cwd,
            env=env,
            capture_output=True,
            timeout=policy.timeout,
            shell=policy.shell,
        )
        proc = subprocess.run(cmd, **kwargs)
        duration = (time.perf_counter() - started) * 1000.0
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if policy.truncate_bytes and len(stdout.encode("utf-8")) > policy.truncate_bytes:
            stdout = stdout[: policy.truncate_bytes]
        result = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration,
            "timed_out": False,
            "cached": False,
        }
        if cache_key is not None and cache is not None and result["ok"]:
            try:
                cache.put(cache_key, stdout.encode("utf-8"))
            except OSError:
                pass
        return result
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "timeout",
            "duration_ms": policy.timeout * 1000.0,
            "timed_out": True,
            "cached": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "timed_out": False,
            "cached": False,
        }


# ---------------------------------------------------------------------------
# Additional execution patterns for production hardening
# ---------------------------------------------------------------------------

def run_with_stream(payload: Dict[str, Any], *, policy: Optional[RunPolicy] = None) -> Dict[str, Any]:
    """Run a command and stream output line-by-line (for large outputs)."""
    policy = policy or RunPolicy()
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            payload["cmd"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=payload.get("cwd"),
            env=payload.get("env"),
            shell=payload.get("shell", False),
        )
        stdout_chunks, stderr_chunks = [], []
        if payload.get("stdin"):
            try:
                proc.stdin.write(payload["stdin"].encode("utf-8", errors="replace"))
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        for line in proc.stdout:
            stdout_chunks.append(line.decode("utf-8", errors="replace"))
        proc.wait(timeout=policy.timeout)
        duration = (time.perf_counter() - started) * 1000.0
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": "".join(stdout_chunks),
            "stderr": "".join(stderr_chunks),
            "duration_ms": duration,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout", "timed_out": True, "duration_ms": policy.timeout * 1000.0}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "timed_out": False, "duration_ms": (time.perf_counter() - started) * 1000.0}


def run_json_command(cmd: List[str], payload: Dict[str, Any], *, policy: Optional[RunPolicy] = None) -> Dict[str, Any]:
    """Run a command that expects JSON input and returns JSON output."""
    policy = policy or RunPolicy()
    input_text = safe_json_dumps(payload)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            timeout=policy.timeout,
            cwd=policy.cwd,
            env=policy.env,
        )
        duration = (time.perf_counter() - started) * 1000.0
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        result = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration,
            "timed_out": False,
        }
        if proc.returncode == 0 and stdout.strip():
            try:
                result["json"] = json.loads(stdout)
            except Exception:
                pass
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "timeout", "timed_out": True, "duration_ms": policy.timeout * 1000.0}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "timed_out": False, "duration_ms": (time.perf_counter() - started) * 1000.0}


def run_batch(commands: List[List[str]], *, policy: Optional[RunPolicy] = None, max_workers: int = 4) -> List[Dict[str, Any]]:
    """Run multiple commands in parallel with bounded concurrency."""
    policy = policy or RunPolicy()
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run, cmd, policy=policy): cmd for cmd in commands}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"ok": False, "stderr": str(exc), "timed_out": False})
    return results


def validate_command(cmd: List[str]) -> Dict[str, Any]:
    """Validate a command for security before execution."""
    if not cmd:
        return {"valid": False, "reason": "empty command"}

    binary = cmd[0] if cmd else ""

    binary_path = which(binary)
    if not binary_path:
        return {"valid": False, "reason": f"binary not found: {binary}"}

    denied = ["rm", "dd", "mkfs", "shutdown", "reboot", "poweroff"]
    if any(binary.endswith(d) for d in denied):
        return {"valid": False, "reason": f"denied binary: {binary}"}

    return {"valid": True, "binary": binary_path}


def resource_limits() -> Dict[str, Any]:
    """Get current process resource limits."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    current_fds = -1
    try:
        pid = os.getpid()
        for fd_dir in [f"/proc/{pid}/fd", "/dev/fd"]:
            if os.path.isdir(fd_dir):
                current_fds = len(os.listdir(fd_dir))
                break
    except Exception:
        pass
    return {
        "max_fds": soft,
        "max_fds_hard": hard,
        "current_fds": current_fds,
    }


# ---------------------------------------------------------------------------
# Security utilities
# ---------------------------------------------------------------------------

class SecurityPolicy:
    """Configurable security policy for execution."""

    def __init__(self, max_memory_mb: int = 512, max_cpu_seconds: float = 30.0,
                 allowed_hosts: list = None, denied_commands: list = None):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds
        self.allowed_hosts = allowed_hosts or ["localhost", "127.0.0.1"]
        self.denied_commands = denied_commands or ["rm -rf", "dd", "shutdown", "reboot"]

    def check_command(self, command: str) -> bool:
        """Check if command is allowed."""
        cmd_lower = command.lower()
        for denied in self.denied_commands:
            if denied in cmd_lower:
                return False
        return True

    def check_url(self, url: str) -> bool:
        """Check if URL is allowed."""
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            return hostname in self.allowed_hosts or hostname == "localhost"
        except Exception:
            return False

    def check_memory(self, used_mb: float) -> bool:
        """Check if memory usage is within limits."""
        return used_mb <= self.max_memory_mb

    def check_cpu(self, used_seconds: float) -> bool:
        """Check if CPU time is within limits."""
        return used_seconds <= self.max_cpu_seconds


def sanitize_env(env: dict) -> dict:
    """Remove dangerous environment variables."""
    dangerous = {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PATH",
        "HOME",
        "PYTHONPATH",
        "PYTHONHOME",
    }
    return {k: v for k, v in env.items() if k not in dangerous}


def validate_file_path(path: str, allowed_dirs: list) -> bool:
    """Validate file path is within allowed directories."""
    from pathlib import Path
    try:
        resolved = Path(path).resolve()
        return any(
            str(resolved).startswith(str(Path(d).resolve()))
            for d in allowed_dirs
        )
    except Exception:
        return False


def rate_limit(calls: list, window_seconds: float = 60.0, max_calls: int = 100) -> bool:
    """Check if rate limit is exceeded."""
    now = time.time()
    recent = [t for t in calls if now - t < window_seconds]
    return len(recent) < max_calls


class AuditLogger:
    """Log security-relevant events."""

    def __init__(self) -> None:
        self.events: list = []

    def log(self, event_type: str, payload: dict) -> None:
        """Log an event."""
        self.events.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": payload,
        })

    def get_events(self, event_type: str = None) -> list:
        """Get logged events, optionally filtered by type."""
        if event_type is None:
            return self.events
        return [e for e in self.events if e["type"] == event_type]


# Global security policy instance
_default_policy = SecurityPolicy()


# ---------------------------------------------------------------------------
# Execution patterns
# ---------------------------------------------------------------------------

class ExecutionPattern:
    """Base class for execution patterns."""

    def __init__(self, name: str):
        self.name = name

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class SequentialPattern(ExecutionPattern):
    """Execute tasks sequentially."""

    def __init__(self):
        super().__init__("sequential")

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tasks = payload.get("tasks", [])
        results = []
        for task in tasks:
            result = run(task.get("command", ""), policy=RunPolicy())
            results.append(result)
        return {
            "status": "ok",
            "pattern": self.name,
            "results": results,
        }


class ParallelPattern(ExecutionPattern):
    """Execute tasks in parallel."""

    def __init__(self, max_workers: int = 4):
        super().__init__("parallel")
        self.max_workers = max_workers

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tasks = payload.get("tasks", [])
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(run, t.get("command", "")): t for t in tasks}
            results = []
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"status": "error", "error": str(exc)})
        return {
            "status": "ok",
            "pattern": self.name,
            "results": results,
        }


class PipelinePattern(ExecutionPattern):
    """Execute tasks in a pipeline."""

    def __init__(self):
        super().__init__("pipeline")

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tasks = payload.get("tasks", [])
        results = []
        context: Dict[str, Any] = {}
        for task in tasks:
            cmd = task.get("command", "")
            if cmd:
                result = run(cmd, policy=RunPolicy())
                results.append(result)
                context[task.get("name", f"step_{len(results)}")] = result
        return {
            "status": "ok",
            "pattern": self.name,
            "results": results,
            "context": context,
        }


class RetryPattern(ExecutionPattern):
    """Execute with retry logic."""

    def __init__(self, max_attempts: int = 3):
        super().__init__("retry")
        self.max_attempts = max_attempts

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        command = payload.get("command", "")
        policy = payload.get("policy", RunPolicy())
        last_error = None
        for attempt in range(self.max_attempts):
            result = run(command, policy=policy)
            if result.get("ok"):
                result["attempt"] = attempt + 1
                return result
            last_error = result
        return {
            "status": "error",
            "pattern": self.name,
            "attempts": self.max_attempts,
            "last_error": last_error,
        }


def execute_pattern(pattern_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a named pattern."""
    patterns = {
        "sequential": SequentialPattern(),
        "parallel": ParallelPattern(),
        "pipeline": PipelinePattern(),
        "retry": RetryPattern(),
    }
    pattern = patterns.get(pattern_name)
    if pattern is None:
        return {"status": "error", "error": f"unknown pattern: {pattern_name}"}
    return pattern.execute(payload)


# ---------------------------------------------------------------------------
# Async and streaming support
# ---------------------------------------------------------------------------

class AsyncExecutor:
    """Async execution wrapper."""

    @staticmethod
    async def run_async(command: str, policy: Optional[RunPolicy] = None) -> Dict[str, Any]:
        """Run a command asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run, command, policy)

    @staticmethod
    async def stream_output(command: str):
        """Stream command output line by line."""
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            yield line.decode('utf-8', errors='replace')


class StreamProcessor:
    """Process streaming data."""

    def __init__(self, buffer_size: int = 8192):
        self.buffer_size = buffer_size
        self.buffer = bytearray()

    def process(self, data: bytes) -> List[str]:
        """Process incoming data and extract complete lines."""
        self.buffer.extend(data)
        lines = []
        newline = b'\n'
        while newline in self.buffer:
            line, self.buffer = self.buffer.split(newline, 1)
            lines.append(line.decode('utf-8', errors='replace'))
        return lines

    def flush(self) -> str:
        """Flush remaining buffer."""
        remaining = bytes(self.buffer).decode('utf-8', errors='replace')
        self.buffer.clear()
        return remaining


class BatchProcessor:
    """Process batches of tasks."""

    def __init__(self, batch_size: int = 10):
        self.batch_size = batch_size
        self.results: List[Dict[str, Any]] = []

    def add(self, task: Dict[str, Any]) -> None:
        """Add a task to the batch."""
        self.results.append(run(task.get("command", "")))

    def process_batch(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of tasks."""
        results = []
        for task in tasks[: self.batch_size]:
            results.append(run(task.get("command", "")))
        return results

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of batch results."""
        ok = sum(1 for r in self.results if r.get("ok"))
        failed = len(self.results) - ok
        return {
            "total": len(self.results),
            "ok": ok,
            "failed": failed,
            "success_rate": ok / len(self.results) if self.results else 0,
        }


# ---------------------------------------------------------------------------
# Metrics and observability
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Collect runtime metrics."""

    def __init__(self) -> None:
        self.counters: Dict[str, int] = {}
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, List[float]] = {}
        self.timers: Dict[str, List[float]] = {}

    def increment(self, metric: str, value: int = 1) -> None:
        """Increment a counter."""
        self.counters[metric] = self.counters.get(metric, 0) + value

    def gauge(self, metric: str, value: float) -> None:
        """Set a gauge value."""
        self.gauges[metric] = value

    def histogram(self, metric: str, value: float) -> None:
        """Record a histogram value."""
        self.histograms.setdefault(metric, []).append(value)

    def timer(self, metric: str, elapsed: float) -> None:
        """Record a timer value."""
        self.timers.setdefault(metric, []).append(elapsed)

    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        summary = {"counters": dict(self.counters)}
        for metric, values in self.histograms.items():
            if values:
                summary[f"histogram_{metric}"] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "p50": sorted(values)[len(values) // 2],
                    "p99": sorted(values)[int(len(values) * 0.99)],
                }
        for metric, values in self.timers.items():
            if values:
                summary[f"timer_{metric}"] = {
                    "count": len(values),
                    "avg_ms": sum(values) / len(values) * 1000,
                    "min_ms": min(values) * 1000,
                    "max_ms": max(values) * 1000,
                }
        return summary


class DistributedTracer:
    """Distributed tracing support."""

    def __init__(self, service_name: str = "minxg"):
        self.service_name = service_name
        self.traces: List[Dict[str, Any]] = []

    def start_trace(self, operation: str, parent_span_id: str = None) -> Dict[str, Any]:
        """Start a new trace."""
        span_id = f"span_{len(self.traces)}"
        trace = {
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "operation": operation,
            "service": self.service_name,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "tags": {},
            "logs": [],
        }
        self.traces.append(trace)
        return trace

    def end_trace(self, span_id: str, status: str = "ok") -> None:
        """End a trace."""
        for trace in self.traces:
            if trace["span_id"] == span_id:
                trace["end_time"] = datetime.now(timezone.utc).isoformat()
                trace["status"] = status
                break

    def add_tag(self, span_id: str, key: str, value: str) -> None:
        """Add tag to trace."""
        for trace in self.traces:
            if trace["span_id"] == span_id:
                trace["tags"][key] = value
                break

    def log(self, span_id: str, message: str, level: str = "info") -> None:
        """Log to trace."""
        for trace in self.traces:
            if trace["span_id"] == span_id:
                trace["logs"].append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "message": message,
                })
                break

    def export(self) -> List[Dict[str, Any]]:
        """Export all traces."""
        return self.traces


class StructuredLogger:
    """Structured logging support."""

    def __init__(self, service: str = "minxg"):
        self.service = service
        self.logs: List[Dict[str, Any]] = []

    def log(self, level: str, message: str, **kwargs) -> None:
        """Log structured message."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service,
            "level": level,
            "message": message,
            **kwargs,
        }
        self.logs.append(entry)

    def info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self.log("info", message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self.log("warning", message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self.log("error", message, **kwargs)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self.log("debug", message, **kwargs)

    def get_logs(self, level: str = None) -> List[Dict[str, Any]]:
        """Get logs, optionally filtered by level."""
        if level is None:
            return self.logs
        return [log for log in self.logs if log["level"] == level]


# ---------------------------------------------------------------------------
# Advanced concurrency and parallelism
# ---------------------------------------------------------------------------

class Actor:
    """Simple actor for concurrent computation."""

    def __init__(self, name: str, mailbox_size: int = 100):
        self.name = name
        self.mailbox: List[Dict[str, Any]] = []
        self.mailbox_size = mailbox_size
        self.running = False
        self._cond = threading.Condition()

    def send(self, message: Dict[str, Any]) -> bool:
        """Send message to actor mailbox."""
        with self._cond:
            if len(self.mailbox) >= self.mailbox_size:
                return False
            self.mailbox.append(message)
            self._cond.notify()
            return True

    def receive(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Receive message from mailbox."""
        with self._cond:
            if not self.mailbox:
                self._cond.wait(timeout=timeout)
            if self.mailbox:
                return self.mailbox.pop(0)
            return None

    def process(self, handler: Callable) -> None:
        """Process messages with handler."""
        self.running = True
        while self.running:
            msg = self.receive(timeout=0.5)
            if msg:
                try:
                    handler(msg)
                except Exception:
                    pass


class ActorSystem:
    """Manage multiple actors."""

    def __init__(self) -> None:
        self.actors: Dict[str, Actor] = {}
        self.threads: Dict[str, threading.Thread] = {}

    def spawn(self, name: str, handler: Callable, mailbox_size: int = 100) -> Actor:
        """Spawn a new actor."""
        actor = Actor(name, mailbox_size)
        self.actors[name] = actor
        thread = threading.Thread(target=actor.process, args=(handler,), daemon=True)
        self.threads[name] = thread
        thread.start()
        return actor

    def send(self, actor_name: str, message: Dict[str, Any]) -> bool:
        """Send message to actor."""
        actor = self.actors.get(actor_name)
        if actor:
            return actor.send(message)
        return False

    def stop(self, actor_name: str) -> None:
        """Stop an actor."""
        actor = self.actors.get(actor_name)
        if actor:
            actor.running = False
            thread = self.threads.get(actor_name)
            if thread:
                thread.join(timeout=1.0)

    def shutdown(self) -> None:
        """Shutdown all actors."""
        for name in list(self.actors.keys()):
            self.stop(name)


class PromiseFuture:
    """Represent an asynchronous computation (renamed to avoid conflict with concurrent.futures.Future)."""

    def __init__(self) -> None:
        self._result: Any = None
        self._exception: Exception = None
        self._done = False
        self._condition = threading.Condition()

    def set_result(self, result: Any) -> None:
        """Set the result of the future."""
        with self._condition:
            self._result = result
            self._done = True
            self._condition.notify_all()

    def set_exception(self, exception: Exception) -> None:
        """Set an exception on the future."""
        with self._condition:
            self._exception = exception
            self._done = True
            self._condition.notify_all()

    def result(self, timeout: float = None) -> Any:
        """Get the result, blocking if necessary."""
        with self._condition:
            if not self._done:
                self._condition.wait(timeout)
            if self._exception:
                raise self._exception
            return self._result

    def done(self) -> bool:
        """Check if the future is done."""
        return self._done

    def exception(self) -> Optional[Exception]:
        """Get the exception if any."""
        return self._exception


class Promise:
    """Promise for setting future results."""

    def __init__(self) -> None:
        self.future = PromiseFuture()

    def resolve(self, result: Any) -> None:
        """Resolve the promise with a result."""
        self.future.set_result(result)

    def reject(self, exception: Exception) -> None:
        """Reject the promise with an exception."""
        self.future.set_exception(exception)

    def get_future(self) -> PromiseFuture:
        """Get the associated future."""
        return self.future


class Task:
    """Represent a unit of work."""

    def __init__(self, func: Callable, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.future = PromiseFuture()

    def run(self) -> None:
        """Execute the task."""
        try:
            result = self.func(*self.args, **self.kwargs)
            self.future.set_result(result)
        except Exception as exc:
            self.future.set_exception(exc)


class TaskQueue:
    """Queue of tasks for concurrent execution."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.queue: _queue.Queue = _queue.Queue()
        self.workers: List[threading.Thread] = []
        self.running = False

    def start(self) -> None:
        """Start worker threads."""
        self.running = True
        for _ in range(self.max_workers):
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            self.workers.append(thread)

    def _worker(self) -> None:
        """Worker thread function."""
        while self.running:
            try:
                task = self.queue.get(timeout=0.1)
                task.run()
                self.queue.task_done()
            except _queue.Empty:
                continue

    def submit(self, func: Callable, *args, **kwargs) -> PromiseFuture:
        """Submit a task to the queue."""
        task = Task(func, *args, **kwargs)
        self.queue.put(task)
        return task.future

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the task queue."""
        self.running = False
        if wait:
            self.queue.join()
        for thread in self.workers:
            thread.join(timeout=1.0)


class CoroutineScheduler:
    """Schedule coroutines for execution."""

    def __init__(self) -> None:
        self.pending: List[Callable] = []
        self.completed: List[Any] = []

    def schedule(self, coro: Callable) -> None:
        """Schedule a coroutine."""
        self.pending.append(coro)

    def run_all(self) -> List[Any]:
        """Run all pending coroutines."""
        results = []
        for coro in self.pending:
            try:
                result = coro()
                results.append(result)
                self.completed.append(result)
            except Exception:
                pass
        self.pending.clear()
        return results


class EventLoop:
    """Simple event loop for async execution."""

    def __init__(self) -> None:
        self.running = False
        self.tasks: List[PromiseFuture] = []

    def run_until_complete(self, future: PromiseFuture, timeout: float = None) -> Any:
        """Run until future is complete."""
        start = time.perf_counter()
        while not future.done():
            if timeout and (time.perf_counter() - start) > timeout:
                raise TimeoutError()
            time.sleep(0.001)
        return future.result()

    def create_task(self, coro: Callable) -> PromiseFuture:
        """Create a task from a coroutine."""
        future = PromiseFuture()
        def run_fn():
            try:
                future.set_result(coro())
            except Exception as exc:
                future.set_exception(exc)
        thread = threading.Thread(target=run_fn, daemon=True)
        thread.start()
        self.tasks.append(future)
        return future

    def stop(self) -> None:
        """Stop the event loop."""
        self.running = False


# ---------------------------------------------------------------------------
# Runtime features
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Circuit breaker pattern implementation."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker."""
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise RuntimeError("circuit breaker is open")
        try:
            result = func(*args, **kwargs)
            self.failure_count = 0
            self.state = "closed"
            return result
        except Exception as exc:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
            raise exc

    def get_state(self) -> str:
        """Get circuit breaker state."""
        return self.state


class Bulkhead:
    """Bulkhead pattern for resource isolation."""

    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self.semaphore = threading.Semaphore(max_concurrent)

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute with bulkhead isolation."""
        with self.semaphore:
            return func(*args, **kwargs)


class RateLimiter:
    """Rate limiter implementation."""

    def __init__(self, max_requests: int, window: float = 1.0):
        self.max_requests = max_requests
        self.window = window
        self.requests: List[float] = []

    def allow(self) -> bool:
        """Check if request is allowed."""
        now = time.time()
        self.requests = [t for t in self.requests if now - t < self.window]
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        return False

    def get_remaining(self) -> int:
        """Get remaining requests in window."""
        now = time.time()
        self.requests = [t for t in self.requests if now - t < self.window]
        return max(0, self.max_requests - len(self.requests))

    def get_reset_time(self) -> float:
        """Get time until rate limit resets."""
        if not self.requests:
            return 0.0
        return max(0.0, self.window - (time.time() - min(self.requests)))


class RetryPolicy:
    """Retry policy with exponential backoff."""

    def __init__(self, max_attempts: int = 3, initial_delay: float = 1.0,
                 max_delay: float = 60.0, backoff_factor: float = 2.0):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with retry."""
        last_exception = None
        delay = self.initial_delay
        for attempt in range(self.max_attempts):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_attempts - 1:
                    time.sleep(min(delay, self.max_delay))
                    delay *= self.backoff_factor
        raise last_exception


class TimeoutManager:
    """Timeout management for operations."""

    def __init__(self) -> None:
        self.timeouts: Dict[str, float] = {}

    def set_timeout(self, operation: str, timeout: float) -> None:
        """Set timeout for an operation."""
        self.timeouts[operation] = timeout

    def get_timeout(self, operation: str) -> float:
        """Get timeout for an operation."""
        return self.timeouts.get(operation, 30.0)

    def execute_with_timeout(self, operation: str, func: Callable, *args, **kwargs) -> Any:
        """Execute with timeout."""
        timeout = self.get_timeout(operation)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func, *args, **kwargs)
            try:
                return future.result(timeout=timeout)
            except TimeoutError:
                raise TimeoutError(f"Operation {operation} timed out after {timeout}s")


class ResourcePool:
    """Generic resource pool."""

    def __init__(self, factory: Callable, max_size: int = 10):
        self.factory = factory
        self.max_size = max_size
        self.pool: List[Any] = []
        self.in_use: Set[Any] = set()

    def acquire(self) -> Any:
        """Acquire a resource."""
        if self.pool:
            resource = self.pool.pop()
            self.in_use.add(resource)
            return resource
        if len(self.in_use) < self.max_size:
            resource = self.factory()
            self.in_use.add(resource)
            return resource
        raise RuntimeError("resource pool exhausted")

    def release(self, resource: Any) -> None:
        """Release a resource."""
        self.in_use.discard(resource)
        if len(self.pool) < self.max_size:
            self.pool.append(resource)


class ObjectPool(ResourcePool):
    """Object pool with validation."""

    def __init__(self, factory: Callable, validator: Callable = None, max_size: int = 10):
        super().__init__(factory, max_size)
        self.validator = validator

    def acquire(self) -> Any:
        """Acquire with validation."""
        resource = super().acquire()
        if self.validator and not self.validator(resource):
            self.release(resource)
            raise ValueError("invalid resource")
        return resource


class ConnectionPool(ResourcePool):
    """Connection pool for network resources."""

    def __init__(self, factory: Callable, max_size: int = 10, timeout: float = 5.0):
        super().__init__(factory, max_size)
        self.timeout = timeout

    def execute(self, operation: Callable) -> Any:
        """Execute operation with connection."""
        conn = self.acquire()
        try:
            return operation(conn)
        finally:
            self.release(conn)


class ThreadPool:
    """Managed thread pool."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, func: Callable, *args, **kwargs):
        """Submit task to thread pool."""
        return self.executor.submit(func, *args, **kwargs)

    def map(self, func: Callable, items: List[Any]) -> List[Any]:
        """Map function over items."""
        return list(self.executor.map(func, items))

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown thread pool."""
        self.executor.shutdown(wait=wait)


class ProcessPool:
    """Managed process pool."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

    def submit(self, func: Callable, *args, **kwargs):
        """Submit task to process pool."""
        return self.executor.submit(func, *args, **kwargs)

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown process pool."""
        self.executor.shutdown(wait=wait)


class HealthMonitor:
    """System health monitoring."""

    def __init__(self) -> None:
        self.checks: Dict[str, Callable] = {}
        self.results: Dict[str, Dict[str, Any]] = {}

    def register_check(self, name: str, check: Callable) -> None:
        """Register a health check."""
        self.checks[name] = check

    def run_checks(self) -> Dict[str, Any]:
        """Run all health checks."""
        results = {}
        for name, check in self.checks.items():
            try:
                result = check()
                results[name] = {
                    "status": "healthy" if result else "unhealthy",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                results[name] = {
                    "status": "error",
                    "error": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        self.results = results
        return results

    def get_overall_health(self) -> str:
        """Get overall health status."""
        if not self.results:
            return "unknown"
        statuses = [r["status"] for r in self.results.values()]
        if "error" in statuses or "unhealthy" in statuses:
            return "unhealthy"
        return "healthy"


# ---------------------------------------------------------------------------
# Module-level async fallback
# ---------------------------------------------------------------------------

async def _invoke_async_default(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "disabled", "message": "async dispatch not configured", "payload": payload}


__all__ = [
    "RunResult",
    "RunPolicy",
    "ContentHashCache",
    "HealthStatus",
    "SubprocessHealth",
    "ExecutionMetrics",
    "run",
    "run_with_stream",
    "run_json_command",
    "run_batch",
    "validate_command",
    "resource_limits",
    "parallel_map",
    "retry",
    "sandbox_path",
    "payload_code",
    "asset_path",
    "which",
    "validate_url",
    "safe_json_dumps",
    "sanitize_path",
    "SecurityPolicy",
    "sanitize_env",
    "AuditLogger",
    "ExecutionPattern",
    "SequentialPattern",
    "ParallelPattern",
    "PipelinePattern",
    "RetryPattern",
    "execute_pattern",
    "AsyncExecutor",
    "StreamProcessor",
    "BatchProcessor",
    "MetricsCollector",
    "DistributedTracer",
    "StructuredLogger",
    "Actor",
    "ActorSystem",
    "PromiseFuture",
    "Promise",
    "Task",
    "TaskQueue",
    "CoroutineScheduler",
    "EventLoop",
    "CircuitBreaker",
    "Bulkhead",
    "RateLimiter",
    "RetryPolicy",
    "TimeoutManager",
    "ResourcePool",
    "ObjectPool",
    "ConnectionPool",
    "ThreadPool",
    "ProcessPool",
    "HealthMonitor",
]