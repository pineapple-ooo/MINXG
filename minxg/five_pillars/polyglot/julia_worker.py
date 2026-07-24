"""JuliaWorker — symbolic / numeric compute via the Julia bridge.

Real-world responsibilities inside MINXG:

* SymbDiff ground-truth: cross-check the Rust core's truncated-Taylor
  ``Jet`` derivatives against Julia's arbitrary-precision arithmetic.
* Self-evolution engine: when a candidate capability proposes a numeric
  formula, execute it in Julia to validate numerically before accepting
  the proposal.
* Driver engine: small-``n`` eigendecomposition is overkill in Rust with
  the LTO build, but Julia's LAPACK-backed ``eigen`` is the reference
  implementation we compare against.

Public tools match the bridge's mode surface (``eval``, ``fib``, ``prime``,
``linsolve``, ``eigen``, ``ode_step``, ``poly``).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from minxg.base import BaseWorker, tool

# Late-bind so test-time mocks can swap the runtime away without
# touching this module — same lazy-ref pattern as ``utils.ensure_config``.
import sys as _sys
_ADAPTER = _sys.modules.get("minxg.contracts.runtime.julia")


def _adapters():
    """Return the live adapter pair (adapter, status)."""
    return _ADAPTER, getattr(_ADAPTER, "ADAPTER_STATUS", "disabled")


class JuliaWorker(BaseWorker):
    worker_id = "julia_math"
    tier = "code"  # v0.18.0 three-tier classification
    version = "0.17.1"

    # ── Tool surface ────────────────────────────────────────────────
    @tool(description="Evaluate a Julia expression via the bridge.",
          category="compute")
    async def julia_eval(self, code: str) -> Dict[str, Any]:
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("evaluate expression", code[:60])
        return await self._invoke_async({"mode": "eval", "code": code})

    @tool(description="Compute Fibonacci(n) using Julia BigInt (O(n)).",
          category="compute")
    async def julia_fib(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 92:
            return self._bad_input("n must be in 0..92", {"n": n})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("fib", f"fib({n})")
        return await self._invoke_async({"mode": "fib", "n": n})

    @tool(description="Count primes <= n using Julia's sieve (n <= 1e7).",
          category="compute")
    async def julia_prime_count(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 10_000_000:
            return self._bad_input("n must be in 0..1e7", {"n": n})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("prime count", f"prime_count({n})")
        return await self._invoke_async({"mode": "prime", "n": n})

    @tool(description="Gaussian-elimination solve of A·x=b (n×n).",
          category="compute")
    async def julia_linsolve(self,
                              n: int,
                              a: List[float],
                              b: List[float]) -> Dict[str, Any]:
        if len(a) != n * n or len(b) != n:
            return self._bad_input("a must be n² entries, b must be n",
                                    {"n": n, "len_a": len(a), "len_b": len(b)})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("linsolve", f"{n}x{n} system")
        return await self._invoke_async({"mode": "linsolve",
                                         "n": n, "a": a, "b": b})

    @tool(description="Symmetric eigenvalues + eigenvectors of an n×n matrix.",
          category="compute")
    async def julia_eigen(self, n: int, a: List[float]) -> Dict[str, Any]:
        if len(a) != n * n:
            return self._bad_input("a must have exactly n² entries",
                                    {"n": n, "len_a": len(a)})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("eigen", f"{n}x{n}")
        return await self._invoke_async({"mode": "eigen",
                                         "n": n, "a": a})

    @tool(description="RK4 single-step integrator for dy/dx=f(x,y), n steps.",
          category="compute")
    async def julia_ode_step(self,
                              f: str,
                              x0: float,
                              y0: float,
                              h: float,
                              n: int) -> Dict[str, Any]:
        if not f:
            return self._bad_input("f expression cannot be empty", {})
        if h <= 0 or n < 0 or n > 100_000:
            return self._bad_input("positive h, 0..1e5 steps required",
                                    {"h": h, "n": n})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("ode_step", f"n={n}")
        return await self._invoke_async({"mode": "ode_step",
                                         "f": f, "x0": x0, "y0": y0,
                                         "h": h, "n": n})

    # ── v0.18.2 additions ─────────────────────────────────────────────

    @tool(description="FFT ground-truth: run Julia FFTW-backed FFT to verify Rust signal.rs output.",
          category="compute")
    async def julia_fft_verify(self, data) -> Dict[str, Any]:
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("fft_verify", f"len={len(data)}")
        return await self._invoke_async({"mode": "fft", "data": list(data)})

    @tool(description="LU decomposition of nxn matrix via Julia LinearAlgebra. Returns L,U,P.",
          category="compute")
    async def julia_lu_decomp(self, n: int, a) -> Dict[str, Any]:
        if len(a) != n * n:
            return self._bad_input("a must have n*n entries", {"n": n, "len_a": len(a)})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("lu_decomp", f"{n}x{n}")
        return await self._invoke_async({"mode": "lu", "n": n, "a": list(a)})

    # ── Helpers ─────────────────────────────────────────────────────
    @staticmethod
    async def _invoke_async(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Off-load blocking subprocess to a worker thread.

        Tools never raise; if the adapter module isn't yet imported
        (``_ADAPTER is None``), return a well-formed disabled envelope so
        callers always see ``language: julia``.
        """
        loop = asyncio.get_running_loop()
        if _ADAPTER is None:
            return {
                "status": "disabled",
                "language": "julia",
                "tool": "unknown",
                "hint": "Julia adapter module not importable; check site-packages.",
            }
        return await loop.run_in_executor(
            None, lambda: _ADAPTER.invoke(payload)
        )

    @staticmethod
    def _disabled(verb: str, example: str) -> Dict[str, Any]:
        return {
            "status": "disabled",
            "language": "julia",
            "tool": verb,
            "hint": (
                "Julia runtime not installed. To enable: install Julia "
                "(pkg install julia on Termux) and add the JSON package "
                "(Pkg.add(\"JSON\")). Then call: minxg runtime-install julia "
                f"--apply. Was attempting: {example}"
            ),
        }

    @staticmethod
    def _bad_input(why: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "error",
            "language": "julia",
            "tool": "input_validation",
            "stderr": why,
            "context": context,
        }

    # ── v0.18.0 expanded tool surface ──────────────────────────────────

    @tool(description="Compute next prime >= n.", category="compute")
    async def julia_next_prime(self, n: int) -> Dict[str, Any]:
        if n < 0 or n > 10**9:
            return self._bad_input("n must be in 0..1e9", {"n": n})
        return await self._invoke_async({"mode": "next_prime", "n": n})

    @tool(description="Least common multiple of a and b.", category="compute")
    async def julia_lcm(self, a: int, b: int) -> Dict[str, Any]:
        return await self._invoke_async({"mode": "lcm", "a": a, "b": b})

    @tool(description="Modular exponentiation (base^exp mod mod).", category="compute")
    async def julia_mod_pow(self, base: int, exp: int, mod: int) -> Dict[str, Any]:
        if mod <= 0:
            return self._bad_input("mod must be positive", {"mod": mod})
        return await self._invoke_async({"mode": "mod_pow", "base": base, "exp": exp, "mod": mod})

    @tool(description="LU decomposition of nxn matrix; returns L, U, P.", category="linear_algebra")
    async def julia_lu_decomp(self, n: int, a: List[float]) -> Dict[str, Any]:
        if len(a) != n * n:
            return self._bad_input("a must have n*n entries", {"n": n, "len_a": len(a)})
        return await self._invoke_async({"mode": "lu", "n": n, "a": a})

    @tool(description="FFT of a real-valued signal using Julia FFTW.", category="signal")
    async def julia_fft(self, signal: List[float]) -> Dict[str, Any]:
        if not signal:
            return self._bad_input("signal cannot be empty", {})
        return await self._invoke_async({"mode": "fft", "signal": signal})

    @tool(description="Optimize a function f(x) using gradient descent or Newton.", category="optimization")
    async def julia_optimize(self, f: str, x0: List[float], method: str = "gradient_descent") -> Dict[str, Any]:
        if not f.strip():
            return self._bad_input("f expression cannot be empty", {})
        if method not in ("gradient_descent", "newton", "bfgs", "lbfgs"):
            return self._bad_input("method must be one of gradient_descent/newton/bfgs/lbfgs", {"method": method})
        return await self._invoke_async({"mode": "optimize", "f": f, "x0": x0, "method": method})

    @tool(description="Statistical test on data (mean, median, std, var, min, max, quantile).", category="stats")
    async def julia_stats(self, data: List[float], test: str = "mean") -> Dict[str, Any]:
        if not data:
            return self._bad_input("data cannot be empty", {})
        return await self._invoke_async({"mode": "stats", "data": data, "test": test})

    @tool(description="Find roots of f(x) = 0 in [a, b] using bisection.", category="numerical")
    async def julia_find_root(self, f: str, a: float, b: float) -> Dict[str, Any]:
        if not f.strip():
            return self._bad_input("f expression cannot be empty", {})
        return await self._invoke_async({"mode": "roots", "f": f, "a": a, "b": b})

    @tool(description="Numerical integration of f(x) from a to b using Simpson's rule.", category="numerical")
    async def julia_integrate(self, f: str, a: float, b: float, n: int = 1000) -> Dict[str, Any]:
        if not f.strip():
            return self._bad_input("f expression cannot be empty", {})
        return await self._invoke_async({"mode": "integrate", "f": f, "a": a, "b": b, "n": n})

    @tool(description="Monte Carlo integration of f(x) over bounds.", category="numerical")
    async def julia_monte_carlo(self, f: str, bounds: List[List[float]], samples: int = 10000) -> Dict[str, Any]:
        if not f.strip():
            return self._bad_input("f expression cannot be empty", {})
        return await self._invoke_async({"mode": "monte", "f": f, "bounds": bounds, "samples": samples})

    @tool(description="Solve a system of linear equations A*x = b.", category="linear_algebra")
    async def julia_solve_linear(self, n: int, a: List[float], b: List[float]) -> Dict[str, Any]:
        if len(a) != n * n or len(b) != n:
            return self._bad_input("a must be n² entries, b must be n", {"n": n, "len_a": len(a), "len_b": len(b)})
        return await self._invoke_async({"mode": "linsolve", "n": n, "a": a, "b": b})

    @tool(description="Compute eigenvalues of symmetric nxn matrix.", category="linear_algebra")
    async def julia_eigen(self, n: int, a: List[float]) -> Dict[str, Any]:
        if len(a) != n * n:
            return self._bad_input("a must have exactly n² entries", {"n": n, "len_a": len(a)})
        return await self._invoke_async({"mode": "eigen", "n": n, "a": a})

    @tool(description="Run a Julia ODE solver (Tsit5) on dy/dt=f(y,t).", category="ode")
    async def julia_ode_solve(self, f: str, tspan: List[float], y0: List[float], h: float = 0.01) -> Dict[str, Any]:
        if not f.strip():
            return self._bad_input("f expression cannot be empty", {})
        if len(tspan) != 2 or tspan[0] >= tspan[1]:
            return self._bad_input("tspan must be [t0, tf] with t0 < tf", {"tspan": tspan})
        return await self._invoke_async({"mode": "diffeq", "f": f, "tspan": tspan, "y0": y0, "h": h})

    @tool(description="Find roots of a polynomial given coefficients.", category="algebra")
    async def julia_poly_roots(self, coeffs: List[float]) -> Dict[str, Any]:
        if not coeffs:
            return self._bad_input("coeffs cannot be empty", {})
        return await self._invoke_async({"mode": "poly", "coeffs": coeffs})

    @tool(description="Matrix multiply A (n×k) by B (k×m).", category="linear_algebra")
    async def julia_matmul(self, n: int, k: int, m: int, a: List[float], b: List[float]) -> Dict[str, Any]:
        if len(a) != n * k or len(b) != k * m:
            return self._bad_input("a must be n*k, b must be k*m", {"n": n, "k": k, "m": m, "len_a": len(a), "len_b": len(b)})
        return await self._invoke_async({"mode": "linalg", "op": "matmul", "a": a, "b": b})

    @tool(description="Compute 2D FFT of a real signal.", category="signal")
    async def julia_fft2(self, signal: List[List[float]]) -> Dict[str, Any]:
        if not signal or not signal[0]:
            return self._bad_input("signal cannot be empty", {})
        flat = [x for row in signal for x in row]
        return await self._invoke_async({"mode": "fft", "signal": flat})

    @tool(description="Run a PDE solver on a 1D grid.", category="pde")
    async def julia_pde_solve(self, eq: str, bc: str, grid: List[float]) -> Dict[str, Any]:
        if not eq.strip() or not bc.strip():
            return self._bad_input("eq and bc cannot be empty", {})
        return await self._invoke_async({"mode": "pde", "eq": eq, "bc": bc, "grid": grid})
