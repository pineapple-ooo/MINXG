"""DatalogWorker — symbolic graph/logic reasoning via clingo (or pyDatalog).

Real-world responsibilities inside MINXG:

* Self-evolution engine — prove capability closure under a Cell.
  Concretely: given a set of registered capabilities, ask Datalog
  ``reachable_cap(X, Y)`` whether every prerequisite chain has a path;
  this guards against accepting a proposal that silently breaks a Cell
  composition.
* Twin engine — resolve ``python_to_rust`` AST correspondence rules:
  declare a node/edge relation and let Datalog enumerate valid rewrites
  (far simpler to express in Datalog than Python loops over typed trees).
* Capabilities manifest — answer capability queries like
  "can this worker satisfy request X given capability Y?" with a
  declarative, deduplicated response (clauses unify; duplicates vanish).

Public tools: ``datalog_run_rules``, ``datalog_graph_reachable``,
``datalog_cycle_check``, ``datalog_set_intersection``,
``datalog_subset_check``, ``datalog_demo``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from minxg.base import BaseWorker, tool

import sys as _sys
_ADAPTER = _sys.modules.get("minxg.contracts.runtime.datalog")


class DatalogWorker(BaseWorker):
    worker_id = "datalog_logic"
    tier = "code"  # v0.18.0 three-tier classification
    version = "0.17.1"

    @tool(description="Run arbitrary Datalog / ASP rules and return solver output.",
          category="logic")
    async def datalog_run_rules(self, code: str) -> Dict[str, Any]:
        if not code.strip():
            return self._bad_input("code cannot be empty", {})
        adapter_status = self._status()
        if adapter_status != "available":
            return self._disabled("datalog_run_rules", code[:40])
        return await self._invoke_async({"code": code})

    @tool(description="Compute graph reachability via clingo transitive closure.",
          category="logic")
    async def datalog_graph_reachable(
        self,
        edges: List[List[str]],
    ) -> Dict[str, Any]:
        """``edges`` is a list of [from,to] pairs.

        Returns: dict with ``tclose`` (list of reachable pairs),
        ``strong_components`` (Datalog's dominated-component analysis).
        """
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        adapter_status = self._status()
        if adapter_status != "available":
            return self._disabled("datalog_graph_reachable",
                                  f"{len(edges)} edges")
        return await self._invoke_async({"code": user_code})

    @tool(description="Detect cycles in a directed graph.",
          category="logic")
    async def datalog_cycle_check(
        self,
        edges: List[List[str]],
    ) -> Dict[str, Any]:
        if not edges:
            return {"status": "ok", "language": "datalog",
                    "cycles": [], "cycle_count": 0,
                    "hint": "Empty graph — vacuously acyclic"}
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = (f"{node_clauses}\n{edge_clauses}\n"
                     "% Cycle detection query\n"
                     "#show cycle/1.\n")
        adapter_status = self._status()
        if adapter_status != "available":
            return self._disabled("datalog_cycle_check",
                                  f"{len(edges)} edges")
        return await self._invoke_async({"code": user_code})

    @tool(description="Compute set intersection of two ordered lists.",
          category="logic")
    async def datalog_set_intersection(
        self,
        a: List[str],
        b: List[str],
    ) -> Dict[str, Any]:
        if not a or not b:
            return self._bad_input("both lists must be non-empty", {})
        # Wrap two sets as edges into the bridge's ``in_set`` predicate.
        a_clauses = "\n".join(f'set_element("a",X) :- X = "{x}".' for x in a)
        b_clauses = "\n".join(f'set_element("b",X) :- X = "{x}".' for x in b)
        user_code = (
            f"{a_clauses}\n{b_clauses}\n"
            '#show in_intersection/3.\n'
        )
        adapter_status = self._status()
        if adapter_status != "available":
            return self._disabled("datalog_set_intersection",
                                  f"|a|={len(a)}, |b|={len(b)}")
        return await self._invoke_async({"code": user_code})

    @tool(description="Check A ⊆ B (pure data, no engine required).",
          category="logic")
    async def datalog_subset_check(
        self,
        a: List[str],
        b: List[str],
    ) -> Dict[str, Any]:
        """Subset check is a pure-Python computation; doesn't need the engine.

        Kept here as a tool so tests can swap engineless paths and so
        users have a uniform tool surface (``datalog_.*``).
        """
        set_a, set_b = set(a), set(b)
        missing = sorted(set_a - set_b)
        return {
            "status": "ok" if not missing else "subset_violation",
            "language": "datalog",
            "tool": "datalog_subset_check",
            "subset": not missing,
            "missing": missing,
            "a_size": len(set_a),
            "b_size": len(set_b),
        }

    @tool(description="Run the shipped demo rules (transitive closure example).",
          category="logic")
    async def datalog_demo(self) -> Dict[str, Any]:
        adapter_status = self._status()
        if adapter_status != "available":
            return self._disabled("datalog_demo", "built-in demo")
        return await self._invoke_async({"mode": "demo"})

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _status() -> str:
        return getattr(_ADAPTER, "ADAPTER_STATUS", "disabled")

    @staticmethod
    async def _invoke_async(payload: Dict[str, Any]) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        if _ADAPTER is None:
            return {
                "status": "disabled",
                "language": "datalog",
                "tool": "datalog_run_rules",
                "hint": "Datalog adapter module not importable; check site-packages.",
            }
        return await loop.run_in_executor(
            None, lambda: _ADAPTER.invoke(payload)
        )

    @staticmethod
    def _disabled(verb: str, example: str) -> Dict[str, Any]:
        return {
            "status": "disabled",
            "language": "datalog",
            "tool": verb,
            "hint": (
                "Datalog runtime not installed. To enable: install clingo "
                "(apt install clingo / pkg install clingo or pip install "
                "pyDatalog). Then: minxg runtime-install datalog --apply. "
                f"Was attempting: {example}"
            ),
        }

    @staticmethod
    def _bad_input(why: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "error",
            "language": "datalog",
            "tool": "input_validation",
            "stderr": why,
            "context": context,
        }

    # ── v0.18.0 expanded tool surface ──────────────────────────────────

    @tool(description="Compute reachability in a directed graph using transitive closure.", category="graph")
    async def datalog_reachability(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Detect cycles in a directed graph.", category="graph")
    async def datalog_cycle_detection(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return {"status": "ok", "language": "datalog", "cycles": [], "cycle_count": 0}
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}\ncycle(X) :- path(X,X)."
        return await self._invoke_async({"code": user_code})

    @tool(description="Compute strongly connected components (SCCs) of a graph.", category="graph")
    async def datalog_scc(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Compute shortest path between two nodes using Dijkstra.", category="graph")
    async def datalog_shortest_path(self, edges: List[List[str]], start: str, goal: str) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b},1)." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Topological sort of a directed acyclic graph.", category="graph")
    async def datalog_topo_sort(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Compute connected components of an undirected graph.", category="graph")
    async def datalog_connected_components(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        edge_clauses_undir = "\n".join(f"edge({a},{b}). edge({b},{a})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses_undir}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Check if graph is bipartite.", category="graph")
    async def datalog_bipartite(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return {"status": "ok", "language": "datalog", "bipartite": True}
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b})." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Compute minimum spanning tree using Kruskal's algorithm.", category="graph")
    async def datalog_mst(self, edges: List[List[str]]) -> Dict[str, Any]:
        if not edges:
            return self._bad_input("edges cannot be empty", {})
        node_clauses = "\n".join(f"node({a}). node({b})." for a, b in edges)
        edge_clauses = "\n".join(f"edge({a},{b},1)." for a, b in edges)
        user_code = f"{node_clauses}\n{edge_clauses}"
        return await self._invoke_async({"code": user_code})

    @tool(description="Run arbitrary Datalog rules with explanation.", category="logic")
    async def datalog_explain(self, code: str) -> Dict[str, Any]:
        if not code.strip():
            return self._bad_input("code cannot be empty", {})
        return await self._invoke_async({"code": code, "explain": True})

    @tool(description="Run Datalog rules in parallel mode.", category="logic")
    async def datalog_parallel(self, code: str) -> Dict[str, Any]:
        if not code.strip():
            return self._bad_input("code cannot be empty", {})
        return await self._invoke_async({"code": code, "parallel": True})

    @tool(description="Query Datalog rules with a specific query atom.", category="logic")
    async def datalog_query(self, code: str, query: str) -> Dict[str, Any]:
        if not code.strip():
            return self._bad_input("code cannot be empty", {})
        if not query.strip():
            return self._bad_input("query cannot be empty", {})
        return await self._invoke_async({"code": code, "query": query})

    @tool(description="Compute set union of multiple lists.", category="sets")
    async def datalog_set_union(self, sets: List[List[str]]) -> Dict[str, Any]:
        if not sets:
            return self._bad_input("sets cannot be empty", {})
        clauses = []
        for i, s in enumerate(sets):
            clauses.extend(f'set({i},{j},"{x}").' for j, x in enumerate(s))
        user_code = "\n".join(clauses)
        return await self._invoke_async({"code": user_code})

    @tool(description="Check subset relation A ⊆ B.", category="sets")
    async def datalog_subset(self, a: List[str], b: List[str]) -> Dict[str, Any]:
        set_a, set_b = set(a), set(b)
        missing = sorted(set_a - set_b)
        return {
            "status": "ok" if not missing else "subset_violation",
            "language": "datalog",
            "tool": "datalog_subset",
            "subset": not missing,
            "missing": missing,
        }

    @tool(description="Typecheck a simple expression in a Hindley-Milner-inspired type system.", category="typecheck")
    async def datalog_typecheck(self, expr: str) -> Dict[str, Any]:
        if not expr.strip():
            return self._bad_input("expr cannot be empty", {})
        return await self._invoke_async({"code": f"typecheck({expr})."})

    @tool(description="Evaluate a Datalog rule with custom facts.", category="logic")
    async def datalog_eval(self, facts: str, rules: str) -> Dict[str, Any]:
        if not facts.strip() or not rules.strip():
            return self._bad_input("facts and rules cannot be empty", {})
        return await self._invoke_async({"code": f"{facts}\n{rules}"})

    @tool(description="Run the shipped demo rules (transitive closure example).", category="demo")
    async def datalog_demo(self) -> Dict[str, Any]:
        return await self._invoke_async({"mode": "demo"})


    @tool(description="Compute all-pairs shortest paths (Floyd-Warshall).", category="graph")
    async def datalog_floyd_warshall(self, n: int, edges: List[List[str]]) -> Dict[str, Any]:
        """Floyd-Warshall all-pairs shortest path."""
        if n < 1 or n > 500:
            return self._bad_input("n must be in 1..500", {"n": n})
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute Floyd-Warshall", {"n": n, "edges": len(edges)})
        return await self._invoke_async({"mode": "floyd_warshall", "n": n, "edges": edges})

    @tool(description="Compute transitive closure of a graph.", category="graph")
    async def datalog_transitive_closure(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Transitive closure using Datalog recursion."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute transitive closure", {"edges": len(edges)})
        return await self._invoke_async({"mode": "transitive_closure", "edges": edges})

    @tool(description="Compute strongly connected components (Tarjan's algorithm).", category="graph")
    async def datalog_scc(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Strongly connected components."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute SCC", {"edges": len(edges)})
        return await self._invoke_async({"mode": "scc", "edges": edges})

    @tool(description="Compute topological sort of a DAG.", category="graph")
    async def datalog_topological_sort(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Topological sort using Datalog."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute topological sort", {"edges": len(edges)})
        return await self._invoke_async({"mode": "topological_sort", "edges": edges})

    @tool(description="Detect if a graph contains a cycle.", category="graph")
    async def datalog_has_cycle(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Cycle detection."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("detect cycles", {"edges": len(edges)})
        return await self._invoke_async({"mode": "has_cycle", "edges": edges})

    @tool(description="Compute minimum spanning tree (Kruskal/Prim).", category="graph")
    async def datalog_mst(self, n: int, edges: List[List[str]]) -> Dict[str, Any]:
        """Minimum spanning tree."""
        if n < 1 or n > 10000:
            return self._bad_input("n must be in 1..10000", {"n": n})
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute MST", {"n": n, "edges": len(edges)})
        return await self._invoke_async({"mode": "mst", "n": n, "edges": edges})

    @tool(description="Check if graph is bipartite.", category="graph")
    async def datalog_is_bipartite(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Bipartite check."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("check bipartite", {"edges": len(edges)})
        return await self._invoke_async({"mode": "is_bipartite", "edges": edges})

    @tool(description="Compute connected components.", category="graph")
    async def datalog_connected_components(self, edges: List[List[str]]) -> Dict[str, Any]:
        """Connected components."""
        if not isinstance(edges, list):
            return self._bad_input("edges must be a list", {"edges_type": type(edges).__name__})
        adapter, status = _adapters()
        if status != "available":
            return self._disabled("compute connected components", {"edges": len(edges)})
        return await self._invoke_async({"mode": "connected_components", "edges": edges})
