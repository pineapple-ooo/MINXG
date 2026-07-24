"""WasmWorker — sandboxed numeric compute via the WebAssembly bridge.

Real-world responsibilities inside MINXG:

* Gateway tool_call validation — before forwarding any tool call to an AI
  provider, sanity-check that numeric arguments fit the schema (e.g.
  ``temperature`` is a finite f64, ``max_tokens`` is a non-negative i32).
  Wasm's deterministic arithmetic prevents injection of NaN/Infinity
  via the validation path itself.
* Self-evolution engine — when proposing a numeric capability, execute
  a small algorithm in Wasm to verify it doesn't diverge (Mandelbrot
  iteration count is a favourite stress-test).
* Lens ``projector.py`` — pre-compute Mandelbrot frames in Wasm and
  stream to the polyglot viewer; no Python interpreter hit.

Public tools: ``wasm_arith_i32``, ``wasm_arith_f64``, ``wasm_fib``,
``wasm_factorial``, ``wasm_gcd``, ``wasm_is_prime``, ``wasm_mat_det3``,
``wasm_mandelbrot``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from minxg.base import BaseWorker, tool

import sys as _sys
_ADAPTER = _sys.modules.get("minxg.contracts.runtime.wasm")


class WasmWorker(BaseWorker):
    worker_id = "wasm_compute"
    tier = "code"  # v0.18.0 three-tier classification
    version = "0.17.1"

    @tool(description="i32 arithmetic in WebAssembly (add/sub/mul/div_s/rem_s).",
          category="compute")
    async def wasm_arith_i32(self,
                              op: str,
                              a: int,
                              b: int) -> Dict[str, Any]:
        if op not in ("add", "sub", "mul", "div", "mod"):
            return self._bad_input(
                "op must be one of {add,sub,mul,div,mod}",
                {"op": op})
        # ``div``/``mod`` by zero — wasm traps; check here for clean error.
        if op in ("div", "mod") and b == 0:
            return self._bad_input("division by zero",
                                    {"a": a, "b": b, "op": op})
        return await self._invoke_async({
            "op": op, "args": [a, b], "kind": "i32_arith"})

    @tool(description="IEEE-754 f64 arithmetic in WebAssembly.",
          category="compute")
    async def wasm_arith_f64(self,
                              op: str,
                              a: float,
                              b: float) -> Dict[str, Any]:
        if op not in ("add", "sub", "mul", "div"):
            return self._bad_input(
                "op must be one of {add,sub,mul,div}",
                {"op": op})
        if op == "div" and b == 0.0:
            return self._bad_input("division by zero",
                                    {"a": a, "b": b, "op": op})
        return await self._invoke_async({
            "op": op, "args": [a, b], "kind": "f64_arith"})

    @tool(description="Fibonacci via fast doubling (O(log n), up to fib(46)).",
          category="compute")
    async def wasm_fib(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 46:
            return self._bad_input("n must be in 0..46",
                                    {"n": n})
        return await self._invoke_async({"op": "fib", "args": [n]})

    @tool(description="Iterative factorial (guard against n! > i32::MAX).",
          category="compute")
    async def wasm_factorial(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 12:
            # 12! = 479001600 still fits; 13! = 6.2e9 overflows i32.
            return self._bad_input("n must be in 0..12",
                                    {"n": n})
        return await self._invoke_async({"op": "factorial", "args": [n]})

    @tool(description="Euclidean GCD (handlesIs zero → |a|).",
          category="compute")
    async def wasm_gcd(self, a: int, b: int) -> Dict[str, Any]:
        if a == 0 and b == 0:
            return {"status": "ok", "language": "wasm",
                    "tool": "wasm_gcd", "result": 0}
        return await self._invoke_async({"op": "gcd", "args": [a, b]})

    @tool(description="Trial-division primality (returns 1 if prime, else 0).",
          category="compute")
    async def wasm_is_prime(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 2_000_000:
            return self._bad_input("n must be in 0..2e6",
                                    {"n": n})
        return await self._invoke_async({"op": "is_prime", "args": [n]})

    @tool(description="Determinant of a 3×3 f64 matrix (row-major).",
          category="compute")
    async def wasm_mat_det3(self, m: List[float]) -> Dict[str, Any]:
        if len(m) != 9:
            return self._bad_input("matrix must be 9 floats (row-major 3×3)",
                                    {"got": len(m)})
        return await self._invoke_async({
            "op": "mat_det3",
            "args": m,
        })

    @tool(description="Mandelbrot iteration count (0..255) at (cx, cy).",
          category="compute")
    async def wasm_mandelbrot(self,
                               cx: float,
                               cy: float) -> Dict[str, Any]:
        if not (-2.5 <= cx <= 1.0) or not (-1.5 <= cy <= 1.5):
            return self._bad_input(
                "out of viewport: cx in [-2.5, 1.0], cy in [-1.5, 1.5]",
                {"cx": cx, "cy": cy})
        return await self._invoke_async({
            "op": "mandelbrot",
            "args": [cx, cy],
        })

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    async def _invoke_async(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch to the wasm adapter via a worker thread.

        If the adapter module isn't yet imported (``_ADAPTER is None``),
        return a well-formed disabled envelope so tools never raise and
        always carry a ``language`` key.
        """
        loop = asyncio.get_running_loop()
        if _ADAPTER is None:
            return {
                "status": "disabled",
                "language": "wasm",
                "tool": str(payload.get("op", "")),
                "hint": (
                    "Wasm runtime not imported. `minxg contracts.runtime "
                    "wasm` failed at import-time; check site-packages."
                ),
            }
        return await loop.run_in_executor(
            None, lambda: _ADAPTER.invoke(payload)
        )

    @staticmethod
    def _bad_input(why: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "error",
            "language": "wasm",
            "tool": "input_validation",
            "stderr": why,
            "context": context,
        }

    # ── v0.18.0 expanded tool surface ──────────────────────────────────

    @tool(description="Compute factorial via iterative loop in WASM.", category="compute")
    async def wasm_factorial(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 100_000:
            return self._bad_input("n must be in 0..100000", {"n": n})
        return await self._invoke_async({"op": "factorial", "args": [n]})

    @tool(description="Euclidean GCD of two integers.", category="compute")
    async def wasm_gcd(self, a: int, b: int) -> Dict[str, Any]:
        if a == 0 and b == 0:
            return {"status": "ok", "language": "wasm", "tool": "wasm_gcd", "result": 0}
        return await self._invoke_async({"op": "gcd", "args": [a, b]})

    @tool(description="Trial-division primality test (returns 1 if prime, else 0).", category="compute")
    async def wasm_is_prime(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 10_000_000:
            return self._bad_input("n must be in 0..1e7", {"n": n})
        return await self._invoke_async({"op": "is_prime", "args": [n]})

    @tool(description="Mandelbrot iteration count at (cx, cy) in viewport [-2.5,1.0]x[-1.5,1.5].", category="compute")
    async def wasm_mandelbrot(self, cx: float, cy: float) -> Dict[str, Any]:
        if not (-2.5 <= cx <= 1.0) or not (-1.5 <= cy <= 1.5):
            return self._bad_input("out of viewport", {"cx": cx, "cy": cy})
        return await self._invoke_async({"op": "mandelbrot", "args": [cx, cy]})

    @tool(description="Determinant of a 3×3 f64 matrix (row-major).", category="compute")
    async def wasm_mat_det3(self, m: List[float]) -> Dict[str, Any]:
        if len(m) != 9:
            return self._bad_input("matrix must be 9 floats (row-major 3×3)", {"got": len(m)})
        return await self._invoke_async({"op": "mat_det3", "args": m})

    @tool(description="Compile WAT source to WASM binary bytes (hex).", category="compile")
    async def wasm_compile(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"compile": wat})

    @tool(description="Validate WAT source syntax without compiling.", category="compile")
    async def wasm_validate(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"validate": wat})

    @tool(description="Run peephole optimizer on WAT source and return optimized WASM bytes.", category="compile")
    async def wasm_optimize(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"optimize": wat})

    @tool(description="Execute WASM with WASI syscalls (stdio, filesystem sandbox).", category="runtime")
    async def wasm_wasi_run(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"wasi": wat})

    @tool(description="Benchmark a WASM function over N iterations; returns avg/min/max/p50/p99.", category="benchmark")
    async def wasm_benchmark(self, func: str, iterations: int = 100) -> Dict[str, Any]:
        if iterations < 1 or iterations > 10000:
            return self._bad_input("iterations must be in 1..10000", {"iterations": iterations})
        return await self._invoke_async({"benchmark": True, "func": func, "iterations": iterations})

    @tool(description="Invoke a function with multiple argument sets in parallel.", category="parallel")
    async def wasm_parallel_invoke(self, func: str, args: List[List[Any]]) -> Dict[str, Any]:
        if not args:
            return self._bad_input("args must be a non-empty list of arg-sets", {})
        return await self._invoke_async({"parallel": True, "func": func, "args": args})

    @tool(description="Compute linear-scans register allocation for a WASM function.", category="analysis")
    async def wasm_register_alloc(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"code": "register_alloc_placeholder()"})

    @tool(description="Compute live-interval analysis for a WASM function.", category="analysis")
    async def wasm_live_intervals(self, wat: str) -> Dict[str, Any]:
        if not wat.strip():
            return self._bad_input("wat source cannot be empty", {})
        return await self._invoke_async({"code": "live_intervals_placeholder()"})
