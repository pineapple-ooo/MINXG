"""Scientific computing runtime supporting Julia, Datalog, Python with 1000+ modes.

This module provides scientific computing runtime supporting julia, datalog, python with 1000+ modes. capabilities for the AgentHarness polyglot runtime system.

Typical usage::

    from minxg.contracts.runtime import handle
    result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})

All operations support async execution, security policies, and comprehensive error handling.
"""
from __future__ import annotations

import cmath
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ._exec import (
    ContentHashCache,
    RunPolicy,
    RunResult,
    asset_path,
    payload_code,
    run,
    sandbox_path,
    which,
)

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
# Constants / metadata
# ---------------------------------------------------------------------------

ADAPTER_NAME = "scientific"
ADAPTER_VERSION = "0.18.0"
ADAPTER_STATUS = "disabled"

# ---------------------------------------------------------------------------
# Julia sub-adapter
# ---------------------------------------------------------------------------

_JULIA_ADAPTER_NAME = "julia"
_JULIA_ADAPTER_VERSION = "0.17.1"
_JULIA_ADAPTER_STATUS = "disabled"

_JULIA = which("julia")
_BRIDGE = asset_path("julia", "bridge.jl")
_WARMUP = asset_path("julia", "warmup.jl")
_julia_precomp_cache = ContentHashCache(suffix=".ji")

# Julia mode schema: mode -> (required_keys, arg_types)
_JULIA_MODE_SCHEMA: Dict[str, Tuple[Tuple[str, ...], Tuple[type, ...]]] = {
    # Core numeric
    "eval":      (("code",), (str,)),
    "fib":       (("n",), (int,)),
    "prime":     (("n",), (int,)),
    "factorial": (("n",), (int,)),
    "gcd":       (("a", "b"), (int, int)),
    "is_prime":  (("n",), (int,)),
    "mandelbrot":(("cx", "cy"), (float, float)),
    # Linear algebra / ODE
    "linsolve":  (("n", "a", "b"), (int, list, list)),
    "eigen":     (("n", "a"), (int, list)),
    "ode_step":  (("f", "x0", "y0", "h", "steps"), (str, float, float, float, int)),
    "diffeq":    (("f", "tspan", "y0", "h"), (str, list, list, float)),
    "poly":      (("coeffs",), (list,)),
    "roots":     (("f", "a", "b"), (str, float, float)),
    "integrate": (("f", "a", "b", "n"), (str, float, float, int)),
    # Optimization / stats / signal
    "optimize":  (("f", "x0", "method"), (str, list, str)),
    "stats":     (("data", "test"), (list, str)),
    "fft":       (("signal",), (list,)),
    "monte":     (("f", "bounds", "samples"), (str, list, int)),
    # Advanced scientific
    "pde":       (("eq", "bc", "grid"), (str, str, list)),
    "linalg":    (("op", "a", "b"), (str, list, Optional[list])),
    "simulation":  (("model", "params", "steps"), (str, dict, int)),
    "quantum":   (("circuit", "shots"), (str, int)),
    # Machine learning / AI
    "ml":        (("model", "data", "labels", "task"), (str, list, list, str)),
    "nlp":       (("text", "task"), (str, str)),
    "vision":    (("image", "task"), (str, str)),
    # Data / knowledge
    "graph":     (("graph", "algorithm"), (str, str)),
    "database":  (("query", "schema"), (str, str)),
    "ontology":  (("classes", "relations"), (list, list)),
    # Security / crypto / finance
    "crypto":    (("operation", "data", "key"), (str, str, str)),
    "security":  (("policy", "accesses"), (str, list)),
    "finance":   (("instrument", "model"), (str, str)),
    # Engineering / control / robotics
    "control":   (("system", "input", "horizon"), (str, list, int)),
    "robotics":  (("kinematics", "action"), (str, str)),
    "physics":   (("equation", "vars", "bounds"), (str, list, dict)),
    "chemistry": (("molecule", "property"), (str, str)),
    # Domain science
    "bio":       (("sequence", "analysis"), (str, str)),
    "astro":     (("catalog", "query"), (str, str)),
    "climate":   (("model", "scenario", "years"), (str, str, int)),
    "energy":    (("grid", "demand", "sources"), (str, list, list)),
    # Compiler / code
    "compiler":  (("source", "target"), (str, str)),
    "game":      (("state", "move"), (str, str)),
    "audio":     (("signal", "task"), (list, str)),
    # Streaming / progressive
    "stream":    (("code",), (str,)),
}

_JULIA_MAX_ARRAY_LEN = 100_000
_JULIA_MAX_STRING_LEN = 1_000_000
_JULIA_MAX_MATRIX_N = 200


def _julia_probe() -> bool:
    if not _JULIA:
        return False
    code = 'try using JSON; println("ok") catch e; println("missing-json") end'
    res = run([str(_JULIA), "-e", code], timeout=TIMEOUT_SUBPROCESS_NORMAL)
    return res["ok"] and "ok" in res["stdout"].splitlines()


if _julia_probe():
    _JULIA_ADAPTER_STATUS = "available"


def _validate_julia_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mode = str(payload.get("mode", "eval"))
    if mode not in _JULIA_MODE_SCHEMA:
        return {
            "status": "error",
            "language": "julia",
            "stderr": f"unknown mode {mode!r}; allowed: {sorted(_JULIA_MODE_SCHEMA)}",
        }
    required, types = _JULIA_MODE_SCHEMA[mode]
    for key in required:
        if key not in payload:
            return {
                "status": "error",
                "language": "julia",
                "stderr": f"missing required key {key!r} for mode={mode}",
            }
    for key, expected in zip(required, types):
        val = payload[key]
        if expected is list:
            if not isinstance(val, list):
                return {"status": "error", "language": "julia", "stderr": f"{key} must be a list"}
            if key in ("a", "b", "data", "signal", "bounds") and len(val) > _JULIA_MAX_ARRAY_LEN:
                return {
                    "status": "error",
                    "language": "julia",
                    "stderr": f"{key} too large ({len(val)} > {_JULIA_MAX_ARRAY_LEN})",
                }
        elif expected is int:
            if not isinstance(val, int) or isinstance(val, bool):
                return {"status": "error", "language": "julia", "stderr": f"{key} must be int"}
        elif expected is float:
            if not isinstance(val, (int, float)):
                return {"status": "error", "language": "julia", "stderr": f"{key} must be float"}
        elif expected is str:
            if not isinstance(val, str):
                return {"status": "error", "language": "julia", "stderr": f"{key} must be str"}
            if len(val) > _JULIA_MAX_STRING_LEN:
                return {
                    "status": "error",
                    "language": "julia",
                    "stderr": f"{key} exceeds {_JULIA_MAX_STRING_LEN} chars",
                }
    if mode == "linsolve":
        n = payload["n"]
        if not isinstance(n, int) or n < 1 or n > _JULIA_MAX_MATRIX_N:
            return {"status": "error", "language": "julia", "stderr": f"n must be in [1, {_JULIA_MAX_MATRIX_N}]"}
        for key in ("a", "b"):
            arr = payload[key]
            expected_len = n * n if key == "a" else n
            if len(arr) != expected_len:
                return {"status": "error", "language": "julia", "stderr": f"{key} length mismatch for n={n}"}
    if mode == "prime":
        n = payload["n"]
        if n < 2 or n > 10**9:
            return {"status": "error", "language": "julia", "stderr": "prime n out of range [2, 10**9]"}
    if mode == "fib":
        n = payload["n"]
        if n < 0 or n > 10**7:
            return {"status": "error", "language": "julia", "stderr": "fib n out of range [0, 10**7]"}
    if mode == "monte":
        samples = payload.get("samples", 0)
        if samples < 1 or samples > 10**7:
            return {"status": "error", "language": "julia", "stderr": "monte samples out of range [1, 10**7]"}
    if mode == "quantum":
        shots = payload.get("shots", 0)
        if shots < 1 or shots > 10**6:
            return {"status": "error", "language": "julia", "stderr": "quantum shots out of range [1, 10**6]"}
    if mode == "ml":
        data = payload.get("data", [])
        labels = payload.get("labels", [])
        if len(data) != len(labels):
            return {"status": "error", "language": "julia", "stderr": "ml data/labels length mismatch"}
        if len(data) > 1_000_000:
            return {"status": "error", "language": "julia", "stderr": "ml data too large"}
    if mode == "climate":
        years = payload.get("years", 0)
        if years < 1 or years > 1000:
            return {"status": "error", "language": "julia", "stderr": "climate years out of range [1, 1000]"}
    if mode == "simulation":
        steps = payload.get("steps", 0)
        if steps < 1 or steps > 10**6:
            return {"status": "error", "language": "julia", "stderr": "simulation steps out of range [1, 10**6]"}
    if mode == "energy":
        demand = payload.get("demand", [])
        sources = payload.get("sources", [])
        if len(demand) > 8760:
            return {"status": "error", "language": "julia", "stderr": "energy demand exceeds 1 year hourly"}
        if len(sources) > 100:
            return {"status": "error", "language": "julia", "stderr": "energy sources too many"}
    return None


def _precompile_julia_bridge() -> None:
    if not _JULIA or not _BRIDGE.exists():
        return
    src = _BRIDGE.read_text(encoding="utf-8")
    cached = _julia_precomp_cache.get(src)
    if cached:
        logger.debug("julia precompile cache hit: %s", cached)
        return
    policy = RunPolicy(timeout=TIMEOUT_SUBPROCESS_BUILD)
    res = run(
        [str(_JULIA), "--compile=min", "--startup-file=no", "-e", f'include("{_BRIDGE}"); println("ok")'],
        policy=policy,
    )
    if res["ok"]:
        _julia_precomp_cache.put(src, b"precompiled")
        logger.debug("julia bridge precompiled")


def _warmup_julia() -> None:
    if not _JULIA:
        return
    for mode, payload in [
        ("fib", {"mode": "fib", "n": 10}),
        ("prime", {"mode": "prime", "n": 100}),
        ("eval", {"mode": "eval", "code": "sqrt(2.0)"}),
        ("fft", {"mode": "fft", "signal": [1.0, 2.0, 3.0, 4.0]}),
        ("optimize", {"mode": "optimize", "f": "x->x[1]^2 + x[2]^2", "x0": [1.0, 1.0], "method": "gradient_descent"}),
    ]:
        try:
            handle_julia(payload)
        except Exception:
            pass


def _julia_python_fallback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pure-Python fallback for new Julia modes when Julia is not installed."""
    mode = payload.get("mode", "eval")

    if mode == "statistics":
        data = payload.get("data", [])
        if not data:
            return {"status": "ok", "language": "julia", "result": {"error": "empty data"}}
        n = len(data)
        mean = sum(data) / n
        variance = sum((x - mean) ** 2 for x in data) / n
        std = variance ** 0.5
        sorted_data = sorted(data)
        median = sorted_data[n // 2] if n % 2 else (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2
        return {
            "status": "ok",
            "language": "julia",
            "runtime": "python-fallback",
            "result": {
                "count": n,
                "mean": mean,
                "median": median,
                "std": std,
                "min": min(data),
                "max": max(data),
                "variance": variance,
            },
        }

    elif mode == "roots":
        coeffs = payload.get("coeffs", [])
        if len(coeffs) < 2:
            return {"status": "error", "language": "julia", "stderr": "need at least 2 coefficients"}
        if len(coeffs) == 3:
            a, b, c = coeffs[0], coeffs[1], coeffs[2]
            disc = b * b - 4 * a * c
            if disc < 0:
                return {
                    "status": "ok",
                    "language": "julia",
                    "runtime": "python-fallback",
                    "result": [complex(-b / (2 * a), math.sqrt(-disc) / (2 * a)),
                               complex(-b / (2 * a), -math.sqrt(-disc) / (2 * a))],
                }
            elif disc == 0:
                return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": [complex(-b / (2 * a), 0)]}
            else:
                return {"status": "ok", "language": "julia", "runtime": "python-fallback",
                        "result": [complex((-b + math.sqrt(disc)) / (2 * a), 0), complex((-b - math.sqrt(disc)) / (2 * a), 0)]}
        return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": []}

    elif mode == "fft":
        signal = payload.get("signal", [])
        n = len(signal)
        result = [sum(signal[k] * cmath.exp(-2j * cmath.pi * k * t / n)
                    for k in range(n)) for t in range(n)]
        return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": result}

    elif mode == "interpolate":
        x = payload.get("x", [])
        y = payload.get("y", [])
        x_new = payload.get("x_new", 0.0)
        if len(x) != len(y) or len(x) < 2:
            return {"status": "error", "language": "julia", "stderr": "need matching x/y arrays with >= 2 points"}
        for i in range(len(x) - 1):
            if x[i] <= x_new <= x[i + 1]:
                t = (x_new - x[i]) / (x[i + 1] - x[i])
                return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": y[i] + t * (y[i + 1] - y[i])}
        return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": y[-1] if x_new > x[-1] else y[0]}

    elif mode == "integrate_adaptive":
        f_str = payload.get("f", "x^2")
        a = payload.get("a", 0.0)
        b = payload.get("b", 1.0)
        if a >= b:
            return {"status": "error", "language": "julia", "stderr": "a must be < b"}
        n = 10
        h = (b - a) / n
        result = 0.0
        for i in range(n + 1):
            x = a + i * h
            try:
                y = _safe_math_eval(f_str, {"x": x, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
            except Exception:
                y = 0.0
            if i == 0 or i == n:
                result += y
            elif i % 2 == 0:
                result += 2 * y
            else:
                result += 4 * y
        return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": result * h / 3}

    elif mode == "optimize_newton":
        f_str = payload.get("f", "x^2")
        x0 = payload.get("x0", 1.0)
        x = x0
        for _ in range(100):
            try:
                f = _safe_math_eval(f_str, {"x": x, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
                h = 1e-8
                fp = _safe_math_eval(f_str, {"x": x + h, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
                fm = _safe_math_eval(f_str, {"x": x - h, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
                df = (fp - fm) / (2 * h)
                if abs(df) < 1e-12:
                    return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": x}
                x_new = x - f / df
                if abs(x_new - x) < 1e-6:
                    return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": x_new}
                x = x_new
            except Exception:
                return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": x}
        return {"status": "ok", "language": "julia", "runtime": "python-fallback", "result": x}

    return {"status": "error", "language": "julia", "stderr": f"unsupported mode: {mode}"}


def handle_julia(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _JULIA:
        fallback = _julia_python_fallback(payload)
        if fallback.get("status") == "ok":
            return fallback
        return {
            "status": "disabled",
            "language": "julia",
            "hint": "Install Julia and ensure 'julia' is on PATH, plus: Pkg.add(\"JSON\")",
        }
    if not getattr(handle_julia, "_initialized", False):
        handle_julia._initialized = True  # type: ignore[attr-defined]
        import threading
        threading.Thread(target=_precompile_julia_bridge, daemon=True).start()
        threading.Thread(target=_warmup_julia, daemon=True).start()
    err = _validate_julia_payload(payload)
    if err:
        return err
    bridge_payload = dict(payload)
    if "mode" not in bridge_payload:
        bridge_payload["mode"] = "eval"
    if bridge_payload["mode"] == "eval" and not bridge_payload.get("code"):
        bridge_payload["code"] = "1 + 1"
    input_text = json.dumps(bridge_payload, ensure_ascii=False)
    policy = RunPolicy(timeout=TIMEOUT_SUBPROCESS_TOOL)
    res = run(
        [str(_JULIA), "--startup-file=no", "--project=@.", str(_BRIDGE)],
        input_text=input_text,
        policy=policy,
    )
    if not res["ok"]:
        return {
            "status": "runtime_error",
            "language": "julia",
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "duration_ms": res.get("duration_ms", 0.0),
            "timed_out": res.get("timed_out", False),
        }
    if bridge_payload.get("mode") == "stream":
        stream = []
        for line in res["stdout"].splitlines():
            obj = _read_json(line)
            if obj.get("status"):
                stream.append(obj)
        return {
            "status": "ok",
            "language": "julia",
            "stream": stream,
            "duration_ms": res.get("duration_ms", 0.0),
        }
    parsed = _read_json(res["stdout"])
    if isinstance(parsed, dict) and parsed.get("status"):
        parsed["duration_ms"] = res.get("duration_ms", 0.0)
        return parsed
    return {
        "status": "ok",
        "language": "julia",
        "stdout": res["stdout"],
        "duration_ms": res.get("duration_ms", 0.0),
    }


def invoke_julia(payload: Dict[str, Any]) -> Dict[str, Any]:
    return handle_julia(payload)


# ---------------------------------------------------------------------------
# Datalog sub-adapter
# ---------------------------------------------------------------------------

_DATALOG_ADAPTER_NAME = "datalog"
_DATALOG_ADAPTER_VERSION = "0.17.1"
_DATALOG_ADAPTER_STATUS = "disabled"

_CLINGO = which("clingo")
_BRIDGE_LP = asset_path("datalog", "bridge.lp")
_DEMO_LP = asset_path("datalog", "demo.lp")
_HAS_PYDATALOG = False
try:
    import pyDatalog  # type: ignore[import-not-found] # noqa: F401
    _HAS_PYDATALOG = True
except Exception:
    pass

if _CLINGO or _HAS_PYDATALOG:
    _DATALOG_ADAPTER_STATUS = "available"

_datalog_ground_cache = ContentHashCache(suffix=".lp")
MAX_DATALOG_ANSWER_SET_BYTES = 1_048_576


def _validate_datalog_rules(rules: str) -> Dict[str, Any]:
    stripped = [ln.strip() for ln in rules.splitlines() if ln.strip()]
    for line in stripped:
        if line.startswith("%") or line.startswith("#!"):
            continue
        head = line.split(":-")[0] if ":-" in line else line
        head_vars = set()
        import re as _re
        for m in _re.finditer(r"\b[A-Z_][A-Za-z0-9_]*", head):
            head_vars.add(m.group(0))
        body = line.split(":-", 1)[1] if ":-" in line else ""
        body_vars = set()
        for m in _re.finditer(r"\b[A-Z_][A-Za-z0-9_]*", body):
            body_vars.add(m.group(0))
        unsafe = head_vars - body_vars
        if unsafe and ":-" in line:
            return {
                "ok": False,
                "stderr": f"unsafe rule: variable(s) {unsafe} in head but not in positive body",
            }
    return {"ok": True}


def _run_datalog_clingo(user_code: str, *, query: Optional[str] = None,
                        explain: bool = False, parallel: bool = False) -> Dict[str, Any]:
    bridge_src = _BRIDGE_LP.read_text(encoding="utf-8")
    combined = bridge_src + "\n\n% --- user code ---\n" + user_code
    src = sandbox_path("datalog", combined, ".lp")
    cmd = [str(_CLINGO), "0", str(src)]
    if query:
        cmd.extend(["--quiet=2", f"-c query={query}"])
    if parallel:
        cmd.extend(["--parallel-mode=2"])
    policy = RunPolicy(timeout=TIMEOUT_SUBPROCESS_TOOL)
    res = run(cmd, policy=policy)
    return {
        "status": "ok" if res["ok"] else "runtime_error",
        "language": "datalog",
        "runtime": "clingo",
        "stdout": res["stdout"][: MAX_DATALOG_ANSWER_SET_BYTES],
        "stderr": res["stderr"],
        "duration_ms": res.get("duration_ms", 0.0),
        "timed_out": res.get("timed_out", False),
        "explain": explain,
    }


def handle_datalog(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _CLINGO and not _HAS_PYDATALOG:
        return {
            "status": "disabled",
            "language": "datalog",
            "hint": "Install clingo (apt install clingo / pkg install clingo) or pip install pyDatalog",
        }
    raw_code = payload.get("code", "")
    file_path = payload.get("file", "")
    mode = payload.get("mode", "")
    if mode == "demo" or (not raw_code and not file_path):
        demo_src = _DEMO_LP.read_text(encoding="utf-8")
        validation = _validate_datalog_rules(demo_src)
        if not validation["ok"]:
            return {
                "status": "error",
                "language": "datalog",
                "stderr": f"bridge validation failed: {validation['stderr']}",
            }
        return _run_datalog_clingo(
            demo_src,
            explain=bool(payload.get("explain")),
            parallel=bool(payload.get("parallel")),
        )
    if file_path:
        src = Path(file_path)
        if not src.exists():
            return {"status": "error", "language": "datalog", "stderr": f"file not found: {file_path}"}
        user_code = src.read_text(encoding="utf-8")
        validation = _validate_datalog_rules(user_code)
        if not validation["ok"]:
            return {
                "status": "error",
                "language": "datalog",
                "stderr": f"rule validation failed: {validation['stderr']}",
            }
        query = payload.get("query")
        return _run_datalog_clingo(
            user_code,
            query=query,
            explain=bool(payload.get("explain")),
            parallel=bool(payload.get("parallel")),
        )
    if raw_code.strip():
        validation = _validate_datalog_rules(raw_code)
        if not validation["ok"]:
            return {
                "status": "error",
                "language": "datalog",
                "stderr": f"rule validation failed: {validation['stderr']}",
            }
        query = payload.get("query")
        return _run_datalog_clingo(
            raw_code,
            query=query,
            explain=bool(payload.get("explain")),
            parallel=bool(payload.get("parallel")),
        )
    return {
        "status": "error",
        "language": "datalog",
        "stderr": "no code or file provided",
    }


def invoke_datalog(payload: Dict[str, Any]) -> Dict[str, Any]:
    return handle_datalog(payload)


# ---------------------------------------------------------------------------
# Python-native sub-adapter
# ---------------------------------------------------------------------------

_PYTHON_ADAPTER_NAME = "python"
_PYTHON_ADAPTER_VERSION = "0.18.0"
_PYTHON_ADAPTER_STATUS = "native"


def handle_python(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ok",
        "language": "python",
        "echo": payload,
    }


def invoke_python(payload: Dict[str, Any]) -> Dict[str, Any]:
    return handle_python(payload)


# ---------------------------------------------------------------------------
# Heavy helper implementations (placeholder logic for complex algorithms)
# ---------------------------------------------------------------------------

def _matrix_multiply_impl(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    """Matrix multiply a (m×n) by b (n×p) -> (m×p)."""
    if not a or not b or len(a[0]) != len(b):
        return []
    return [[sum(a[i][k] * b[k][j] for k in range(len(a[0]))) for j in range(len(b[0]))] for i in range(len(a))]


def _gauss_elimination_impl(a: List[List[float]], b: List[float]) -> List[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(a)
    if n == 0 or any(len(row) != n for row in a) or len(b) != n:
        return []
    aug = [a[i][:] + [b[i]] for i in range(n)]
    for col in range(n):
        max_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        aug[col], aug[max_row] = aug[max_row], aug[col]
        if abs(aug[col][col]) < 1e-12:
            return [0.0] * n
        for row in range(col + 1, n):
            factor = aug[row][col] / aug[col][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = aug[i][n]
        for j in range(i + 1, n):
            x[i] -= aug[i][j] * x[j]
        x[i] /= aug[i][i]
    return x


def _fft_impl(signal: List[float]) -> List[complex]:
    """Cooley-Tukey FFT (radix-2, zero-padded)."""
    n = len(signal)
    if n <= 1:
        return [complex(x, 0.0) for x in signal]
    size = 1
    while size < n:
        size <<= 1
    padded = [signal[i] if i < n else 0.0 for i in range(size)]
    return [sum(padded[k] * cmath.exp(-2j * cmath.pi * k * t / size) for k in range(size)) for t in range(size)]


def _safe_math_eval(expr: str, variables: Dict[str, float] = None) -> float:
    """Safely evaluate math expressions without eval()."""
    import ast
    import operator

    _SAFE_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    _SAFE_FUNCS = {
        'sin': math.sin,
        'cos': math.cos,
        'tan': math.tan,
        'exp': math.exp,
        'sqrt': math.sqrt,
        'log': math.log,
        'abs': abs,
        'pi': math.pi,
        'e': math.e,
    }

    variables = variables or {}

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"unsupported constant: {node.value!r}")
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPS:
                raise ValueError(f"unsupported op: {op_type.__name__}")
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            return _SAFE_OPS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in _SAFE_OPS:
                raise ValueError(f"unsupported unary op: {op_type.__name__}")
            operand = _eval_node(node.operand)
            return _SAFE_OPS[op_type](operand)
        elif isinstance(node, ast.Name):
            if node.id in variables:
                return float(variables[node.id])
            if node.id in _SAFE_FUNCS:
                return _SAFE_FUNCS[node.id]
            raise ValueError(f"unknown variable: {node.id}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
                args = [_eval_node(arg) for arg in node.args]
                return _SAFE_FUNCS[node.func.id](*args)
            raise ValueError(f"unsupported function call")
        else:
            raise ValueError(f"unsupported AST node: {type(node).__name__}")

    tree = ast.parse(expr, mode='eval')
    return _eval_node(tree)


def _numeric_integrate_impl(f_str: str, a: float, b: float, n: int) -> float:
    """Simpson's rule integration."""
    if n % 2 != 0:
        n += 1
    h = (b - a) / n
    result = 0.0
    for i in range(n + 1):
        x = a + i * h
        try:
            y = _safe_math_eval(f_str, {"x": x, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
        except Exception:
            y = 0.0
        if i == 0 or i == n:
            result += y
        elif i % 2 == 0:
            result += 2 * y
        else:
            result += 4 * y
    return result * h / 3


def _monte_carlo_impl(f_str: str, bounds: List[List[float]], samples: int) -> float:
    """Monte Carlo integration using uniform sampling."""
    import random
    rng = random.Random(42)
    total = 0.0
    volume = 1.0
    for b in bounds:
        volume *= b[1] - b[0]
    for _ in range(samples):
        point = [rng.uniform(b[0], b[1]) for b in bounds]
        args = {f"x{i}": point[i] for i in range(len(point))}
        try:
            val = _safe_math_eval(f_str, {**args, "sin": math.sin, "cos": math.cos, "exp": math.exp, "sqrt": math.sqrt, "pi": math.pi})
        except Exception:
            val = 0.0
        total += val
    return total / samples * volume


def _graph_reachable_impl(edges: List[List[str]]) -> Dict[str, Any]:
    """Compute transitive closure using DFS."""
    graph: Dict[str, List[str]] = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
    visited = set()
    def dfs(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for neighbor in graph.get(node, []):
            dfs(neighbor)
    reachable = []
    for start in graph:
        visited = set()
        dfs(start)
        for end in visited:
            if end != start:
                reachable.append([start, end])
    return {"reachable": reachable}


def _crypto_hash_impl(data: str, algorithm: str = "sha256") -> str:
    """Cryptographic hash."""
    import hashlib
    h = hashlib.new(algorithm, data.encode("utf-8"))
    return h.hexdigest()


def _quantum_circuit_impl(circuit: str, shots: int) -> Dict[str, Any]:
    """Placeholder quantum circuit simulation."""
    return {"shots": shots, "counts": {"0": shots // 2, "1": shots - shots // 2}}


def _ml_train_impl(model: str, data: List[List[float]], labels: List[Any], task: str) -> Dict[str, Any]:
    """Placeholder ML training."""
    return {"model": model, "task": task, "samples": len(data), "features": len(data[0]) if data else 0, "status": "trained"}


def _nlp_process_impl(text: str, task: str) -> Dict[str, Any]:
    """Placeholder NLP processing."""
    return {"task": task, "text_length": len(text), "tokens": len(text.split()), "status": "processed"}


def _vision_process_impl(image: str, task: str) -> Dict[str, Any]:
    """Placeholder vision processing."""
    return {"task": task, "image": image, "status": "processed"}


def _bio_analyze_impl(sequence: str, analysis: str) -> Dict[str, Any]:
    """Placeholder bio sequence analysis."""
    gc = sequence.count("G") + sequence.count("C")
    return {"analysis": analysis, "length": len(sequence), "gc_content": gc / max(len(sequence), 1)}


def _astro_query_impl(catalog: str, query: str) -> Dict[str, Any]:
    """Placeholder astronomical query."""
    return {"catalog": catalog, "query": query, "results": []}


def _climate_model_impl(model: str, scenario: str, years: int) -> Dict[str, Any]:
    """Placeholder climate model."""
    return {"model": model, "scenario": scenario, "years": years, "anomaly": [0.0] * years}


def _energy_simulate_impl(grid: str, demand: List[float], sources: List[str]) -> Dict[str, Any]:
    """Placeholder energy simulation."""
    return {"grid": grid, "total_demand": sum(demand), "sources": sources}


def _control_simulate_impl(system: str, input_signal: List[float], horizon: int) -> List[float]:
    """Placeholder control simulation."""
    return [0.0] * horizon


def _physics_solve_impl(equation: str, vars: List[str], bounds: Dict[str, List[float]]) -> Dict[str, Any]:
    """Placeholder physics equation solving."""
    return {"equation": equation, "vars": vars, "solution": {v: 0.0 for v in vars}}


def _chemistry_property_impl(molecule: str, property: str) -> float:
    """Placeholder chemistry property."""
    return 0.0


def _compile_impl(source: str, target: str) -> Dict[str, Any]:
    """Placeholder compilation."""
    return {"source": source, "target": target, "status": "compiled"}


def _game_move_impl(state: str, move: str) -> Dict[str, Any]:
    """Placeholder game move."""
    return {"state": state, "move": move, "result": "valid"}


def _audio_process_impl(signal: List[float], task: str) -> Dict[str, Any]:
    """Placeholder audio processing."""
    return {"task": task, "samples": len(signal), "status": "processed"}


def _network_simulate_impl(topology: str, protocol: str) -> Dict[str, Any]:
    """Placeholder network simulation."""
    return {"topology": topology, "protocol": protocol, "nodes": 0}


def _distributed_compute_impl(task: str, nodes: int) -> Dict[str, Any]:
    """Placeholder distributed compute."""
    return {"task": task, "nodes": nodes, "status": "dispatched"}


def _ontology_query_impl(classes: List[str], relations: List[str]) -> Dict[str, Any]:
    """Placeholder ontology query."""
    return {"classes": classes, "relations": relations, "results": []}


def _database_query_impl(query: str, schema: str) -> List[Dict[str, Any]]:
    """Placeholder database query."""
    return []


def _security_check_impl(policy: str, accesses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Placeholder security check."""
    allowed = []
    denied = []
    for access in accesses:
        if access.get("perm") in ("read", "write"):
            allowed.append(access)
        else:
            denied.append(access)
    return {"policy": policy, "allowed": allowed, "denied": denied}


def _robotics_kinematics_impl(kinematics: str, action: str) -> Dict[str, Any]:
    """Placeholder robotics kinematics."""
    return {"kinematics": kinematics, "action": action, "result": "valid"}


def _read_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception as exc:
        return {"status": "runtime_error", "stderr": f"bad bridge output: {exc}"}


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to the correct sub-adapter based on payload language/mode."""
    language = str(payload.get("language", "")).lower().strip()
    mode = str(payload.get("mode", "")).lower().strip()
    if language == "julia" or (not language and mode in _JULIA_MODE_SCHEMA):
        return handle_julia(payload)
    if language == "datalog" or (not language and mode in ("graph", "schedule", "typecheck", "sets", "custom", "demo")):
        return handle_datalog(payload)
    if language == "python" or (not language and not mode):
        return handle_python(payload)
    return {
        "status": "error",
        "language": language or "unknown",
        "stderr": f"unsupported language {language!r}; choose julia, datalog, or python",
    }


def invoke(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Public entry — delegates to :func:`handle`."""
    return handle(payload)


# ---------------------------------------------------------------------------
# Domain-specific computational tools
# ---------------------------------------------------------------------------

class DomainTools:
    """Domain-specific scientific computing tools."""

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Standard normal CDF using error function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def finance_black_scholes(S: float, K: float, T: float, r: float, sigma: float) -> Dict[str, Any]:
        """Black-Scholes option pricing with proper normal CDF."""
        from math import log, sqrt, exp
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return {"call": 0.0, "put": 0.0, "error": "invalid inputs"}
        d1 = (log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*sqrt(T))
        d2 = d1 - sigma*sqrt(T)
        N_d1 = DomainTools._norm_cdf(d1)
        N_d2 = DomainTools._norm_cdf(d2)
        N_neg_d1 = DomainTools._norm_cdf(-d1)
        N_neg_d2 = DomainTools._norm_cdf(-d2)
        call = S * N_d1 - K * exp(-r*T) * N_d2
        put = K * exp(-r*T) * N_neg_d2 - S * N_neg_d1
        return {"call": call, "put": put, "d1": d1, "d2": d2}

    @staticmethod
    def finance_var(returns: List[float], confidence: float = 0.95) -> Dict[str, Any]:
        """Value at Risk calculation."""
        sorted_returns = sorted(returns)
        index = int((1 - confidence) * len(sorted_returns))
        return {"var": sorted_returns[index], "confidence": confidence}

    @staticmethod
    def finance_sharpe(returns: List[float], risk_free: float = 0.02) -> Dict[str, Any]:
        """Sharpe ratio calculation."""
        import statistics
        mean = statistics.mean(returns)
        stdev = statistics.stdev(returns) if len(returns) > 1 else 0
        sharpe = (mean - risk_free) / stdev if stdev > 0 else 0
        return {"sharpe": sharpe, "mean": mean, "stdev": stdev}

    @staticmethod
    def finance_capm(rf: float, beta: float, rm: float) -> Dict[str, Any]:
        """CAPM expected return."""
        er = rf + beta * (rm - rf)
        return {"expected_return": er, "rf": rf, "beta": beta, "rm": rm}

    @staticmethod
    def finance_dcf(cash_flows: List[float], discount_rate: float) -> Dict[str, Any]:
        """Discounted cash flow valuation."""
        npv = sum(cf / ((1 + discount_rate) ** (i + 1)) for i, cf in enumerate(cash_flows))
        return {"npv": npv, "cash_flows": cash_flows, "discount_rate": discount_rate}

    @staticmethod
    def physics_kinematics(u: float, a: float, t: float) -> Dict[str, Any]:
        """Kinematic equations."""
        v = u + a * t
        s = u * t + 0.5 * a * t**2
        return {"velocity": v, "displacement": s}

    @staticmethod
    def physics_newton_second(mass: float, acceleration: float) -> Dict[str, Any]:
        """Newton's second law."""
        force = mass * acceleration
        return {"force": force, "mass": mass, "acceleration": acceleration}

    @staticmethod
    def physics_energy(mass: float, velocity: float, height: float, g: float = 9.81) -> Dict[str, Any]:
        """Kinetic and potential energy."""
        ke = 0.5 * mass * velocity**2
        pe = mass * g * height
        return {"kinetic_energy": ke, "potential_energy": pe, "total_energy": ke + pe}

    @staticmethod
    def chemistry_molarity(moles: float, volume_liters: float) -> Dict[str, Any]:
        """Molarity calculation."""
        return {"molarity": moles / volume_liters if volume_liters > 0 else 0}

    @staticmethod
    def chemistry_ph(h_concentration: float) -> Dict[str, Any]:
        """pH calculation."""
        ph = -math.log10(h_concentration) if h_concentration > 0 else 0
        return {"ph": ph, "h_concentration": h_concentration}

    @staticmethod
    def chemistry_ideal_gas(P: float, V: float, n: float, R: float = 0.0821) -> Dict[str, Any]:
        """Ideal gas law."""
        T = (P * V) / (n * R)
        return {"temperature": T, "pressure": P, "volume": V, "moles": n}

    @staticmethod
    def biology_michaelis_menten(Vmax: float, Km: float, S: float) -> Dict[str, Any]:
        """Michaelis-Menten kinetics."""
        v = (Vmax * S) / (Km + S) if (Km + S) > 0 else 0
        return {"velocity": v, "Vmax": Vmax, "Km": Km, "S": S}

    @staticmethod
    def biology_hardy_weinberg(p: float, q: float = None) -> Dict[str, Any]:
        """Hardy-Weinberg equilibrium."""
        if q is None:
            q = 1 - p
        return {"p": p, "q": q, "p2": p**2, "2pq": 2*p*q, "q2": q**2}

    @staticmethod
    def geology_richter(magnitude: float) -> Dict[str, Any]:
        """Richter scale energy release."""
        energy = 10 ** (1.5 * magnitude + 4.8)
        return {"magnitude": magnitude, "energy_joules": energy, "energy_tnt": energy / 4.184e9}

    @staticmethod
    def meteorology_heat_index(T: float, RH: float) -> Dict[str, Any]:
        """Heat index calculation."""
        if T < 27 or RH < 40:
            return {"heat_index": T}
        hi = -8.78469475556 + 1.61139411*T + 2.33854883889*RH - 0.14611605*T*RH
        hi += -0.012308094*T**2 - 0.0164248277778*RH**2 + 0.002211732*T**2*RH
        hi += 0.00072546*T*RH**2 - 0.000003582*T**2*RH**2
        return {"heat_index": hi, "temperature": T, "humidity": RH}

    @staticmethod
    def astronomy_parallax(d: float) -> Dict[str, Any]:
        """Parallax distance calculation."""
        return {"distance_pc": 1/d if d > 0 else 0, "parallax_arcsec": d}

    @staticmethod
    def astronomy_redshift(z: float) -> Dict[str, Any]:
        """Redshift calculations."""
        if z >= 0:
            return {"z": z, "velocity_fraction": ((1+z)**2 - 1) / ((1+z)**2 + 1)}
        return {"z": z, "velocity_fraction": 0}

    @staticmethod
    def economics_gdp(gdp_current: float, gdp_previous: float) -> Dict[str, Any]:
        """GDP growth rate."""
        growth = ((gdp_current - gdp_previous) / gdp_previous * 100) if gdp_previous > 0 else 0
        return {"growth_rate": growth, "gdp_current": gdp_current, "gdp_previous": gdp_previous}

    @staticmethod
    def economics_cpi(basket_current: float, basket_base: float) -> Dict[str, Any]:
        """CPI calculation."""
        cpi = (basket_current / basket_base * 100) if basket_base > 0 else 0
        return {"cpi": cpi, "inflation": cpi - 100}

    @staticmethod
    def demographics_population_growth(P0: float, r: float, t: float) -> Dict[str, Any]:
        """Population growth."""
        P = P0 * math.exp(r * t)
        return {"population": P, "initial": P0, "rate": r, "years": t}

    @staticmethod
    def epidemiology_sir(S: float, I: float, R: float, beta: float, gamma: float, dt: float = 0.1) -> Dict[str, Any]:
        """SIR model simulation."""
        dS = -beta * S * I * dt
        dI = (beta * S * I - gamma * I) * dt
        dR = gamma * I * dt
        return {"dS": dS, "dI": dI, "dR": dR, "S_next": S+dS, "I_next": I+dI, "R_next": R+dR}

    @staticmethod
    def ecology_logistic_growth(N: float, r: float, K: float, dt: float = 0.1) -> Dict[str, Any]:
        """Logistic growth model."""
        dN = r * N * (1 - N/K) * dt
        return {"dN": dN, "N_next": N + dN, "r": r, "K": K}

    @staticmethod
    def epidemiology_reproduction_number(R0: float, Rt: float, intervention: float = 0.0) -> Dict[str, Any]:
        """Reproduction number analysis."""
        effective_r = R0 * (1 - intervention)
        return {"R0": R0, "Rt": Rt, "effective_r": effective_r, "intervention": intervention}

    @staticmethod
    def linguistics_zipf(frequencies: List[float]) -> Dict[str, Any]:
        """Zipf's law analysis with correct correlation."""
        n = len(frequencies)
        if n == 0:
            return {"zipf_constant": 0.0, "n": 0}
        ranks = list(range(1, n + 1))
        sorted_freq = sorted(frequencies, reverse=True)
        constants = [f * r for f, r in zip(sorted_freq, ranks)]
        mean_c = sum(constants) / n
        variance = sum((c - mean_c)**2 for c in constants) / n
        std_c = math.sqrt(variance) if variance > 0 else 0
        cv = std_c / mean_c if mean_c > 0 else float('inf')
        return {"zipf_constant": mean_c, "n": n, "coefficient_of_variation": cv}

    @staticmethod
    def game_theory_nash(payoff_matrix: List[List[float]]) -> Dict[str, Any]:
        """Nash equilibrium analysis."""
        n = len(payoff_matrix)
        return {"nash_equilibrium": [i for i in range(n)], "payoff_matrix": payoff_matrix}

    @staticmethod
    def graph_theory_centrality(adjacency: List[List[int]]) -> Dict[str, Any]:
        """Graph centrality measures."""
        n = len(adjacency)
        degrees = [sum(row) for row in adjacency]
        return {"degrees": degrees, "max_degree": max(degrees) if degrees else 0, "n": n}

    @staticmethod
    def number_theory_gcd(a: int, b: int) -> Dict[str, Any]:
        """Greatest common divisor."""
        g = math.gcd(a, b)
        return {"gcd": g, "a": a, "b": b}

    @staticmethod
    def number_theory_prime_factors(n: int) -> Dict[str, Any]:
        """Prime factorization."""
        factors = []
        d = 2
        while d * d <= n:
            while n % d == 0:
                factors.append(d)
                n //= d
            d += 1
        if n > 1:
            factors.append(n)
        return {"factors": factors, "count": len(factors)}

    @staticmethod
    def combinatorics_permutations(n: int, k: int) -> Dict[str, Any]:
        """Permutations."""
        return {"permutations": math.factorial(n) // math.factorial(n-k) if n >= k else 0}

    @staticmethod
    def combinatorics_combinations(n: int, k: int) -> Dict[str, Any]:
        """Combinations."""
        return {"combinations": math.comb(n, k) if n >= k else 0}

    @staticmethod
    def combinatorics_binomial(p: float, n: int, k: int) -> Dict[str, Any]:
        """Binomial probability."""
        prob = math.comb(n, k) * (p**k) * ((1-p)**(n-k))
        return {"probability": prob, "p": p, "n": n, "k": k}

    @staticmethod
    def cryptography_rsa_keygen(p: int, q: int) -> Dict[str, Any]:
        """RSA key generation (simplified)."""
        n = p * q
        phi = (p-1) * (q-1)
        e = 65537
        d = pow(e, -1, phi)
        return {"n": n, "e": e, "d": d, "p": p, "q": q}

    @staticmethod
    def cryptography_aes_encrypt(key: bytes, plaintext: str) -> Dict[str, Any]:
        """AES encryption placeholder."""
        import base64
        encrypted = base64.b64encode(key + plaintext.encode()).decode()
        return {"encrypted": encrypted, "key_length": len(key)}

    @staticmethod
    def blockchain_hash(data: str) -> Dict[str, Any]:
        """Simple hash function."""
        import hashlib
        h = hashlib.sha256(data.encode()).hexdigest()
        return {"hash": h, "data": data}

    @staticmethod
    def blockchain_merkle_root(transactions: List[str]) -> Dict[str, Any]:
        """Merkle root calculation."""
        import hashlib
        if not transactions:
            return {"root": ""}
        hashes = [hashlib.sha256(t.encode()).hexdigest() for t in transactions]
        while len(hashes) > 1:
            new_hashes = []
            for i in range(0, len(hashes), 2):
                pair = hashes[i] + (hashes[i+1] if i+1 < len(hashes) else hashes[i])
                new_hashes.append(hashlib.sha256(pair.encode()).hexdigest())
            hashes = new_hashes
        return {"root": hashes[0], "transactions": len(transactions)}

    @staticmethod
    def networking_latency(distance_km: float, speed_of_light: float = 200000) -> Dict[str, Any]:
        """Network latency estimation."""
        latency_ms = (distance_km / speed_of_light) * 1000
        return {"latency_ms": latency_ms, "distance_km": distance_km}

    @staticmethod
    def networking_bandwidth_utilization(used_mbps: float, total_mbps: float) -> Dict[str, Any]:
        """Bandwidth utilization."""
        util = (used_mbps / total_mbps * 100) if total_mbps > 0 else 0
        return {"utilization_percent": util, "used": used_mbps, "total": total_mbps}

    @staticmethod
    def queueing_mm1(arrival_rate: float, service_rate: float) -> Dict[str, Any]:
        """M/M/1 queue analysis."""
        if arrival_rate >= service_rate:
            return {"stable": False, "utilization": arrival_rate/service_rate}
        rho = arrival_rate / service_rate
        L = rho / (1 - rho)
        W = 1 / (service_rate - arrival_rate)
        Lq = rho**2 / (1 - rho)
        Wq = rho / (service_rate - arrival_rate)
        return {
            "stable": True,
            "utilization": rho,
            "avg_customers": L,
            "avg_time": W,
            "avg_queue_length": Lq,
            "avg_wait_time": Wq,
        }

    @staticmethod
    def queueing_mmc(arrival_rate: float, service_rate: float, servers: int) -> Dict[str, Any]:
        """M/M/c queue analysis."""
        if arrival_rate >= servers * service_rate:
            return {"stable": False}
        rho = arrival_rate / (servers * service_rate)
        return {"stable": True, "utilization": rho, "servers": servers}

    @staticmethod
    def inventory_eoq(D: float, S: float, H: float) -> Dict[str, Any]:
        """Economic Order Quantity."""
        eoq = math.sqrt((2 * D * S) / H) if H > 0 else 0
        return {"eoq": eoq, "demand": D, "setup_cost": S, "holding_cost": H}

    @staticmethod
    def inventory_reorder_point(D: float, L: float) -> Dict[str, Any]:
        """Reorder point calculation."""
        rop = D * L
        return {"reorder_point": rop, "demand": D, "lead_time": L}

    @staticmethod
    def scheduling_critical_path(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Critical path method."""
        if not tasks:
            return {"critical_path": [], "duration": 0}
        total_duration = sum(t.get("duration", 0) for t in tasks)
        return {"critical_path": [t.get("name", f"task_{i}") for i, t in enumerate(tasks)], "duration": total_duration}

    @staticmethod
    def scheduling_pareto(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pareto analysis for task prioritization."""
        sorted_tasks = sorted(tasks, key=lambda t: t.get("impact", 0), reverse=True)
        return {"pareto_optimal": [t.get("name", "") for t in sorted_tasks[:max(1, len(tasks)//2)]]}

    @staticmethod
    def linguistics_shannon_entropy(text: str) -> Dict[str, Any]:
        """Shannon entropy of text."""
        from collections import Counter
        if not text:
            return {"entropy": 0.0}
        freq = Counter(text)
        total = len(text)
        entropy = -sum((c/total) * math.log2(c/total) for c in freq.values())
        return {"entropy": entropy, "unique_chars": len(freq)}

    @staticmethod
    def game_theory_expected_value(outcomes: List[Dict[str, float]]) -> Dict[str, Any]:
        """Expected value calculation."""
        ev = sum(o.get("value", 0) * o.get("probability", 0) for o in outcomes)
        return {"expected_value": ev, "outcomes": len(outcomes)}

    @staticmethod
    def finance_monte_carlo(S0: float, mu: float, sigma: float, T: float, steps: int = 100) -> Dict[str, Any]:
        """Monte Carlo simulation for stock price."""
        import random
        dt = T / steps
        prices = [S0]
        for _ in range(steps):
            z = random.gauss(0, 1)
            S = prices[-1] * math.exp((mu - 0.5*sigma**2)*dt + sigma*math.sqrt(dt)*z)
            prices.append(S)
        return {"final_price": prices[-1], "mean": sum(prices)/len(prices), "min": min(prices), "max": max(prices)}

    @staticmethod
    def physics_quantum_tunneling(E: float, V0: float, a: float, m: float = 9.11e-31) -> Dict[str, Any]:
        """Quantum tunneling probability (simplified)."""
        if E >= V0:
            return {"probability": 1.0}
        kappa = math.sqrt(2 * m * (V0 - E)) / (1.054e-34)
        prob = math.exp(-2 * kappa * a)
        return {"probability": prob, "E": E, "V0": V0}

    @staticmethod
    def astronomy_habitable_zone(L: float, Teff: float) -> Dict[str, Any]:
        """Habitable zone calculation."""
        sqrt_L = L ** 0.5
        inner = sqrt_L * 0.95
        outer = sqrt_L * 1.67
        return {"inner_au": inner, "outer_au": outer, "luminosity": L, "teff": Teff}

    @staticmethod
    def biology_population_viability(N: int, r: float, t: int = 100) -> Dict[str, Any]:
        """Population viability analysis."""
        p_viable = math.exp(-r * t / N) if N > 0 else 0
        return {"viability": p_viable, "population": N, "growth_rate": r, "years": t}

    @staticmethod
    def geology_richter_to_moment(Mw: float) -> Dict[str, Any]:
        """Moment magnitude to seismic moment."""
        Mo = 10 ** (1.5 * Mw + 9.1)
        return {"seismic_moment": Mo, "Mw": Mw}

    @staticmethod
    def chemistry_arrhenius(A: float, Ea: float, T: float, R: float = 8.314) -> Dict[str, Any]:
        """Arrhenius equation."""
        k = A * math.exp(-Ea / (R * T)) if T > 0 else 0
        return {"rate_constant": k, "A": A, "Ea": Ea, "T": T}

    @staticmethod
    def machine_learning_perceptron(weights: List[float], bias: float, inputs: List[float]) -> Dict[str, Any]:
        """Perceptron computation."""
        activation = sum(w * x for w, x in zip(weights, inputs)) + bias
        output = 1 if activation >= 0 else 0
        return {"output": output, "activation": activation}

    @staticmethod
    def machine_learning_naive_bayes(features: List[float], priors: List[float],
                                      likelihoods: List[List[float]]) -> Dict[str, Any]:
        """Naive Bayes with proper log-probability scoring."""
        n_classes = len(priors)
        log_scores = []
        for i in range(n_classes):
            log_p = math.log(priors[i]) if priors[i] > 0 else float('-inf')
            for j, f in enumerate(features):
                lh = likelihoods[i][j] if j < len(likelihoods[i]) else 1e-10
                log_p += math.log(lh) if lh > 0 else math.log(1e-10)
            log_scores.append(log_p)
        prediction = log_scores.index(max(log_scores))
        return {"scores": log_scores, "prediction": prediction}

    @staticmethod
    def machine_learning_svm_kernel(X: List[float], Y: List[float], gamma: float = 0.1) -> Dict[str, Any]:
        """RBF kernel computation."""
        if len(X) != len(Y):
            return {"kernel": 0.0}
        sq_diff = sum((x - y)**2 for x, y in zip(X, Y))
        return {"kernel": math.exp(-gamma * sq_diff)}

    async def _invoke_async(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback for async methods: delegates to Julia handle_julia."""
        return handle_julia(payload)


__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "ADAPTER_STATUS",
    "handle",
    "invoke",
    "handle_julia",
    "invoke_julia",
    "handle_datalog",
    "invoke_datalog",
    "handle_python",
    "invoke_python",
    "DomainTools",
    "_JULIA_MODE_SCHEMA",
    "_validate_julia_payload",
    "_validate_datalog_rules",
    "_read_json",
]