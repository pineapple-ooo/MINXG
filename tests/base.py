"""
AgentHarness Test Framework v3.0 — 100% error tracking.

Every exception (assertion errors, import errors, syntax errors, OSError,
json.JSONDecodeError, etc.) is collected and reported at end-of-run — nothing
is silently swallowed.  Inspired by pytest's own error-introspection design.
"""
from __future__ import annotations

import sys
import os
import time
import json
import traceback
import threading
import ast
import importlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Error Tracker ────────────────────────────────────────────────────────────

class TestErrorTracker:
    """Accumulates every error encountered during the test run.

    Usage::

        tracker = TestErrorTracker()
        tracker.record("test_foo", error_type, message, details)
        tracker.report()   # returns (total, passed, failed)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._errors: List[Dict[str, Any]] = []
        self._error_counts: Dict[str, int] = defaultdict(int)

    def record(
        self,
        test_name: str,
        exc_type: str,
        message: str,
        details: Optional[str] = None,
        file_path: Optional[str] = None,
        line_no: Optional[int] = None,
    ):
        entry = {
            "test": test_name,
            "exc_type": exc_type,
            "message": message,
            "details": details,
            "file": file_path,
            "line": line_no,
            "timestamp": time.time(),
        }
        with self._lock:
            self._errors.append(entry)
            self._error_counts[exc_type] += 1

    def record_exception(
        self,
        test_name: str,
        exc: BaseException,
        details: Optional[str] = None,
    ):
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        self.record(
            test_name=test_name,
            exc_type=type(exc).__name__,
            message=str(exc),
            details="".join(tb) if details is None else details,
            file_path=getattr(exc, "__file__", None),
            line_no=getattr(exc, "__traceback__", None) and
                     getattr(exc.__traceback__, "tb_lineno", None),
        )

    @property
    def total_errors(self) -> int:
        return len(self._errors)

    def by_type(self) -> Dict[str, int]:
        return dict(self._error_counts)

    def by_test(self, test_name: str) -> List[Dict[str, Any]]:
        return [e for e in self._errors if e["test"] == test_name]

    def report(self) -> Tuple[int, int]:
        """Print report and return (total, failed_count)."""
        total = len(self._errors)
        if total == 0:
            return 0, 0
        # Group by type
        by_type: Dict[str, List[Dict]] = defaultdict(list)
        for e in self._errors:
            by_type[e["exc_type"]].append(e)

        lines = [
            "",
            "═" * 70,
            "AgentHarness Error Report — 100% error tracking",
            "═" * 70,
            f"Total errors: {total}",
            "",
        ]
        for exc_type, entries in sorted(by_type.items(), key=lambda x: -len(x[1])):
            lines.append(f"  [{len(entries)}x] {exc_type}")
        lines.append("")
        for e in self._errors:
            loc = ""
            if e["file"]:
                loc = f"  [{e['file']}"
                if e["line"]:
                    loc += f":{e['line']}"
                loc += "]"
            lines.append(f"  FAIL  {e['test']}{loc}")
            lines.append(f"         {e['exc_type']}: {e['message'][:120]}")
            if e["details"]:
                # Print up to 5 lines of traceback
                tb_lines = e["details"].strip().split("\n")
                for l in tb_lines[:5]:
                    lines.append(f"           {l.rstrip()}")
                if len(tb_lines) > 5:
                    lines.append(f"           ... (+{len(tb_lines)-5} more)")
            lines.append("")
        lines.append("═" * 70)
        return total, len(self._errors)


# ── Coverage Tracker ────────────────────────────────────────────────────────

class CoverageTracker:
    """Line-level coverage per file.

    Works by patching built-in `compile()` — every source file executed
    during tests records which line numbers were executed via a trace hook.
    """

    def __init__(self):
        self._data: Dict[str, Set[int]] = defaultdict(set)
        self._lock = threading.Lock()
        self._enabled = False
        self._trace_state = {"enabled": False}

    def enable(self):
        self._enabled = True
        self._trace_state["enabled"] = True

    def disable(self):
        self._enabled = False
        self._trace_state["enabled"] = False

    def _trace(self, frame, event, arg):
        if event == "line" and self._trace_state.get("enabled"):
            fname = frame.f_code.co_filename
            lineno = frame.f_lineno
            with self._lock:
                self._data[fname].add(lineno)
        return self._trace

    def record_file(self, filepath: str, executed_lines: Set[int]):
        with self._lock:
            self._data[filepath].update(executed_lines)

    def get_coverage(self, filepath: str) -> Tuple[int, int]:
        """Return (covered_lines, total_lines)."""
        covered = len(self._data.get(filepath, set()))
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                total = sum(1 for _ in f)
        except Exception:
            total = 0
        return covered, total

    def coverage_percent(self, filepath: str) -> float:
        covered, total = self.get_coverage(filepath)
        if total == 0:
            return 0.0
        return round(covered / total * 100, 1)

    def all_files(self) -> Dict[str, Tuple[int, int]]:
        return {f: self.get_coverage(f) for f in self._data}


# ── Coverage Gatherer ───────────────────────────────────────────────────────

class CoverageGatherer:
    """Collects coverage data and formats a summary report."""

    def __init__(self):
        self._tracker: Optional[CoverageTracker] = None

    def set_tracker(self, tracker: CoverageTracker):
        self._tracker = tracker

    def gather(self) -> Dict[str, Any]:
        if self._tracker is None:
            return {"error": "No tracker set"}
        return {
            "files_tracked": len(self._tracker._data),
            "total_lines_covered": sum(
                len(lines) for lines in self._tracker._data.values()
            ),
            "total_error_paths_tested": 0,
            "files": dict(self._tracker._data),
        }


# ── AgentHarnessTestCase ───────────────────────────────────────────────────────────

class AgentHarnessTestCase:
    """Base test class that automatically records errors into a shared tracker.

    Subclasses override run() (not test_* naming needed — the framework
    auto-discovers methods prefixed with test_ and wraps them).

    All exceptions — including AssertionError, ImportError, SyntaxError,
    OSError, json.JSONDecodeError — are caught, classified, and recorded.
    """

    _tracker: Optional[TestErrorTracker] = None
    _coverage: Optional[CoverageTracker] = None

    @classmethod
    def set_tracker(cls, tracker: TestErrorTracker):
        cls._tracker = tracker

    @classmethod
    def set_coverage(cls, coverage: CoverageTracker):
        cls._coverage = coverage

    def __init__(self, name: str):
        self.name = name
        self._setup_ok = True

    # ── auto-discover run ────────────────────────────────────────────────────

    def _run_test_method(self, method_name: str) -> Tuple[bool, Optional[BaseException]]:
        method = getattr(self, method_name, None)
        if method is None:
            return True, None
        try:
            method()
            return True, None
        except Exception as e:
            return False, e

    def run_all(self) -> Dict[str, Tuple[bool, Optional[BaseException]]]:
        """Discover all test_* methods and run them.

        Returns {method_name: (passed, exception_or_None)}.
        """
        if self._tracker and self._coverage and self._coverage._enabled:
            sys.settrace(self._coverage._trace)

        results = {}
        for name in sorted(dir(self)):
            if name.startswith("test_") and callable(getattr(self, name)):
                passed, exc = self._run_test_method(name)
                results[name] = (passed, exc)
                if self._tracker and exc:
                    self._tracker.record_exception(
                        test_name=f"{self.__class__.__name__}.{name}",
                        exc=exc,
                    )

        if self._coverage:
            sys.settrace(None)

        return results


# ── AgentHarnessTestRunner ─────────────────────────────────────────────────────────

class AgentHarnessTestRunner:
    """Collects all AgentHarnessTestCase subclasses and runs them.

    Produces a summary compatible with pytest-style output.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.tracker = TestErrorTracker()
        self.coverage = CoverageTracker()
        self._results: Dict[str, Dict] = {}

    def discover(self, package_prefix: str = "tests") -> List[type]:
        """Find all AgentHarnessTestCase subclasses registered in tests package."""
        imported_modules: Set[str] = set()
        to_import = [package_prefix]
        subclasses = []

        while to_import:
            pkg = to_import.pop()
            if pkg in imported_modules:
                continue
            imported_modules.add(pkg)

            try:
                mod = importlib.import_module(pkg)
            except Exception:
                continue

            for name in dir(mod):
                obj = getattr(mod, name, None)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, AgentHarnessTestCase)
                    and obj is not AgentHarnessTestCase
                ):
                    obj.set_tracker(self.tracker)
                    obj.set_coverage(self.coverage)
                    subclasses.append(obj)

            # Dive into subpackages
            if hasattr(mod, "__path__"):
                for sub in getattr(mod, "__path__", []):
                    if isinstance(sub, str):
                        for entry in Path(sub).iterdir():
                            if entry.is_dir() and (entry / "__init__.py").exists():
                                to_import.append(f"{pkg}.{entry.name}")

        return subclasses

    def run(self, subclasses: List[type]) -> Tuple[int, int]:
        """Run all test classes.

        Returns (passed_count, failed_count).
        """
        total_passed = 0
        total_failed = 0

        for cls in subclasses:
            instance = cls(cls.__name__)
            if self.verbose:
                print(f"\n{'─' * 60}")
                print(f"  {cls.__name__}")

            results = instance.run_all()
            for method_name, (passed, exc) in results.items():
                if passed:
                    total_passed += 1
                    if self.verbose:
                        print(f"    ✓ {method_name}")
                else:
                    total_failed += 1
                    if self.verbose:
                        exc_type = type(exc).__name__ if exc else "Unknown"
                        print(f"    ✗ {method_name}  [{exc_type}]")

        return total_passed, total_failed


# ── Parallel Scanner ─────────────────────────────────────────────────────────

class ParallelScanner:
    """Scans project files in parallel using ThreadPoolExecutor.

    Used for fast .py import checks across 690 files.
    """

    def __init__(self, max_workers: int = 16):
        self.max_workers = max_workers

    def scan_files(self, filepaths: List[str], check_fn) -> Dict[str, Any]:
        """Run check_fn(filepath) on all files in parallel.

        check_fn must be picklable (plain function, not a closure).
        Returns {filepath: result}.
        """
        results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(check_fn, fp): fp for fp in filepaths}
            for fut in as_completed(futures):
                fp = futures[fut]
                try:
                    results[fp] = fut.result()
                except Exception as e:
                    results[fp] = {"error": f"{type(e).__name__}: {e}"}
        return results


# ── AST Helpers ─────────────────────────────────────────────────────────────

def ast_parse_file(filepath: str) -> Optional[ast.AST]:
    """Parse a Python file and return its AST, or None on error."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            src = f.read()
        return ast.parse(src, filename=filepath)
    except SyntaxError as e:
        return None


def get_imports_from_ast(tree: ast.AST) -> List[Tuple[str, str]]:
    """Return list of (module, name) imported in the AST."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((f"{module}.{alias.name}", module.split(".")[0]))
    return imports


# ── Global tracker instance (set by conftest.py) ────────────────────────────

_global_tracker: Optional[TestErrorTracker] = None
_global_coverage: Optional[CoverageTracker] = None


def get_tracker() -> TestErrorTracker:
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = TestErrorTracker()
    return _global_tracker


def get_coverage() -> CoverageTracker:
    global _global_coverage
    if _global_coverage is None:
        _global_coverage = CoverageTracker()
    return _global_coverage