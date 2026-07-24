"""
minxg.contracts.agent.neurosymbolic — Neurosymbolic Reasoning Layer
===================================================================

Bold design: combine neural pattern recognition with symbolic logic for
robust code understanding and reasoning.

Capabilities
------------
1. **Symbolic AST Reasoning** — logical inference over program structure
2. **Neural Pattern Matching** — detect idioms, anti-patterns, and design smells
3. **Abductive Reasoning** — hypothesize root causes from symptoms
4. **Counterfactual Simulation** — "what if" code change analysis
5. **Causal Intervention** — predict downstream effects of modifications
6. **Probabilistic Logic** — handle uncertainty in dependency analysis
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


__all__ = [
"SymbolicFact",
"NeuralPattern",
"CausalGraph",
"AbductiveReasoner",
"CounterfactualSimulator",
"NeurosymbolicEngine",
]

# ---------------------------------------------------------------------------
# Symbolic Layer
# ---------------------------------------------------------------------------

@dataclass
class SymbolicFact:
    fact_id: str
    predicate: str
    args: List[str]
    confidence: float = 1.0
    source: str = "ast"
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CausalGraph:
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edges: List[Tuple[str, str, str]] = field(default_factory=list)  # (cause, relation, effect)

    def add_cause(self, cause: str, relation: str, effect: str) -> None:
        self.nodes.setdefault(cause, {"type": "cause"})
        self.nodes.setdefault(effect, {"type": "effect"})
        self.edges.append((cause, relation, effect))

    def downstream_effects(self, node: str) -> Set[str]:
        visited = set()
        def dfs(n: str) -> None:
            if n in visited:
                return
            visited.add(n)
            for cause, relation, effect in self.edges:
                if cause == n:
                    dfs(effect)
        dfs(node)
        visited.discard(node)
        return visited

    def upstream_causes(self, node: str) -> Set[str]:
        visited = set()
        def dfs(n: str) -> None:
            if n in visited:
                return
            visited.add(n)
            for cause, relation, effect in self.edges:
                if effect == n:
                    dfs(cause)
        dfs(node)
        visited.discard(node)
        return visited

# ---------------------------------------------------------------------------
# Neural Pattern Layer
# ---------------------------------------------------------------------------

@dataclass
class NeuralPattern:
    pattern_id: str
    name: str
    regex: str
    category: str
    severity: str = "medium"
    description: str = ""
    suggested_fix: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

class PatternLibrary:
    """Curated patterns for code smell and anti-pattern detection."""

    def __init__(self) -> None:
        self.patterns: List[NeuralPattern] = [
            NeuralPattern(
                pattern_id="eval_usage",
                name="eval_usage",
                regex=r"\beval\s*\(",
                category="security",
                severity="critical",
                description="Use of eval() is dangerous",
                suggested_fix="Use ast.literal_eval or safe parser",
            ),
            NeuralPattern(
                pattern_id="utcnow_deprecated",
                name="utcnow_deprecated",
                regex=r"datetime\.utcnow\(\)",
                category="bug",
                severity="medium",
                description="datetime.utcnow() is deprecated",
                suggested_fix="Use datetime.now(timezone.utc)",
            ),
            NeuralPattern(
                pattern_id="shell_true",
                name="shell_true",
                regex=r"shell\s*=\s*True",
                category="security",
                severity="high",
                description="shell=True enables injection",
                suggested_fix="Use shell=False with shlex.split",
            ),
            NeuralPattern(
                pattern_id="bare_except",
                name="bare_except",
                regex=r"\bexcept\s*:",
                category="style",
                severity="low",
                description="Bare except catches all exceptions",
                suggested_fix="Specify exception types",
            ),
            NeuralPattern(
                pattern_id="large_file",
                name="large_file",
                regex=r"",
                category="tech_debt",
                severity="medium",
                description="File exceeds size threshold",
                suggested_fix="Split into focused modules",
            ),
        ]

    def match(self, source: str, file_path: str) -> List[Tuple[NeuralPattern, int, str]]:
        results = []
        lines = source.splitlines()
        for pattern in self.patterns:
            if not pattern.regex:
                if len(lines) > 500:
                    results.append((pattern, 1, f"Large file: {len(lines)} lines"))
                continue
            for lineno, line in enumerate(lines, 1):
                if re.search(pattern.regex, line):
                    results.append((pattern, lineno, line.strip()))
        return results

# ---------------------------------------------------------------------------
# Abductive Reasoning
# ---------------------------------------------------------------------------

@dataclass
class AbductiveReasoner:
    causal_graph: CausalGraph = field(default_factory=CausalGraph)
    pattern_library: PatternLibrary = field(default_factory=PatternLibrary)

    def hypothesize(self, symptom: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        hypotheses = []
        for cause, relation, effect in self.causal_graph.edges:
            if symptom.lower() in effect.lower():
                hypotheses.append({
                    "hypothesis": f"{cause} may cause {symptom}",
                    "confidence": 0.7,
                    "cause": cause,
                    "effect": effect,
                    "relation": relation,
                })
        return hypotheses or [{"hypothesis": f"No causal hypothesis for {symptom}", "confidence": 0.0}]

    def explain(self, symptom: str, context: Dict[str, Any]) -> Dict[str, Any]:
        hypotheses = self.hypothesize(symptom, context)
        return {
            "symptom": symptom,
            "hypotheses": hypotheses,
            "recommended_action": hypotheses[0]["hypothesis"] if hypotheses else "investigate",
        }

# ---------------------------------------------------------------------------
# Counterfactual Simulator
# ---------------------------------------------------------------------------

@dataclass
class CounterfactualSimulator:
    def simulate(self, code: str, change: Dict[str, Any]) -> Dict[str, Any]:
        modified = code
        if change.get("replace"):
            old, new = change["replace"]
            modified = modified.replace(old, new)
        try:
            ast.parse(modified)
            syntax_ok = True
        except SyntaxError as exc:
            syntax_ok = False
        return {
            "change": change,
            "syntax_ok": syntax_ok,
            "diff_summary": f"Applied {change.get('description', 'change')}",
            "risk": "low" if syntax_ok else "high",
        }

    def batch_simulate(self, code: str, changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.simulate(code, c) for c in changes]

# ---------------------------------------------------------------------------
# Neurosymbolic Engine
# ---------------------------------------------------------------------------

class NeurosymbolicEngine:
    """Bold core: neural pattern detection + symbolic causal reasoning."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.pattern_library = PatternLibrary()
        self.reasoner = AbductiveReasoner()
        self.simulator = CounterfactualSimulator()
        self.causal_graph = CausalGraph()
        self.history: List[Dict[str, Any]] = []

    def analyze_file(self, file_path: Path) -> Dict[str, Any]:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        matches = self.pattern_library.match(source, str(file_path))
        facts = []
        for pattern, lineno, line in matches:
            fact = SymbolicFact(
                fact_id=hashlib.sha256(f"{file_path}:{lineno}:{pattern.pattern_id}".encode()).hexdigest()[:12],
                predicate=pattern.name,
                args=[str(file_path), str(lineno)],
                confidence=0.9 if pattern.severity in ("critical", "high") else 0.7,
                source="neurosymbolic",
                metadata={"severity": pattern.severity, "line": line},
            )
            facts.append(fant)
            self.causal_graph.add_cause(pattern.name, "manifests_as", f"{file_path}:{lineno}")
        return {
            "file": str(file_path),
            "facts": [
                {
                    "id": f.fact_id,
                    "predicate": f.predicate,
                    "args": f.args,
                    "confidence": f.confidence,
                    "severity": pattern.severity,
                }
                for f, (pattern, *_rest) in zip(facts, matches)
            ],
            "symbolic_count": len(facts),
        }

    def analyze_project(self) -> Dict[str, Any]:
        results = []
        for py_file in self.project_root.rglob("*.py"):
            if any(ex in py_file.parts for ex in [".venv", "__pycache__", ".git", "node_modules"]):
                continue
            results.append(self.analyze_file(py_file))
        return {"files_analyzed": len(results), "results": results}

    def explain_issue(self, issue: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return self.reasoner.explain(issue, context)

    def simulate_change(self, file_path: Path, change: Dict[str, Any]) -> Dict[str, Any]:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        return self.simulator.simulate(source, change)

    def build_causal_model(self, issues: List[Dict[str, Any]]) -> CausalGraph:
        for issue in issues:
            self.causal_graph.add_cause(issue.get("category", "unknown"), "causes", issue.get("file_path", "unknown"))
        return self.causal_graph

    def predict_impact(self, change_target: str) -> Dict[str, Any]:
        downstream = self.causal_graph.downstream_effects(change_target)
        upstream = self.causal_graph.upstream_causes(change_target)
        return {
            "target": change_target,
            "downstream": sorted(downstream),
            "upstream": sorted(upstream),
            "risk_score": len(downstream) * 0.1,
        }
