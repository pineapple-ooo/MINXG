"""Versioned manifest with capabilities, tool counts, and release artifacts.

This module provides versioned manifest with capabilities, tool counts, and release artifacts. capabilities for the AgentHarness polyglot runtime system.

Typical usage::

    from agent_harness.contracts.runtime import handle
    result = handle({"language": "julia", "mode": "eval", "code": "sqrt(4.0)"})

All operations support async execution, security policies, and comprehensive error handling.
"""
from __future__ import annotations

import hashlib
import json
import platform as _platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._exec import HealthStatus, validate_url

# ---------------------------------------------------------------------------
# Canonical language list
# ---------------------------------------------------------------------------

POLYGLOT_LANGUAGES: List[str] = [
    "python",   # always-on, native Python ecosystem
    "wasm",     # WebAssembly sandboxed compute (WASI)
    "julia",    # Julia numerical / scientific
    "datalog",  # Datalog logic programme for rules/queries
]


# ---------------------------------------------------------------------------
# Per-language metadata
# ---------------------------------------------------------------------------

@dataclass
class LanguageManifest:
    """Extended metadata for one polyglot language."""
    name: str
    version: str
    status: str  # native | sandbox | managed
    module: str
    adapter_version: str = ""
    expected_binary_version: str = ""
    capabilities: List[str] = field(default_factory=list)
    tool_count: int = 0
    checksum: str = ""
    dependencies: List[str] = field(default_factory=list)
    documentation_url: str = ""
    binary_names: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "module": self.module,
            "adapter_version": self.adapter_version,
            "expected_binary_version": self.expected_binary_version,
            "capabilities": self.capabilities,
            "tool_count": self.tool_count,
            "checksum": self.checksum,
            "dependencies": self.dependencies,
            "documentation_url": self.documentation_url,
            "binary_names": self.binary_names,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LanguageManifest":
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "0.0.0")),
            status=str(data.get("status", "unknown")),
            module=str(data.get("module", "")),
            adapter_version=str(data.get("adapter_version", "")),
            expected_binary_version=str(data.get("expected_binary_version", "")),
            capabilities=list(data.get("capabilities", [])),
            tool_count=int(data.get("tool_count", 0)),
            checksum=str(data.get("checksum", "")),
            dependencies=list(data.get("dependencies", [])),
            documentation_url=str(data.get("documentation_url", "")),
            binary_names=list(data.get("binary_names", [])),
        )

    def verify_checksum(self, data: bytes) -> bool:
        """Verify that ``data`` matches the expected checksum."""
        if not self.checksum:
            return True
        return hashlib.sha256(data).hexdigest().startswith(self.checksum)


def _build_manifest() -> Dict[str, LanguageManifest]:
    """Construct the full language manifest from static metadata."""
    return {
        "python": LanguageManifest(
            name="python",
            version="0.18.0",
            status="native",
            module="agent_harness.contracts.runtime.python",
            adapter_version="0.18.0",
            capabilities=["eval", "scripting", "glue"],
            tool_count=1,
            dependencies=[],
            documentation_url="https://docs.python.org/3/",
            binary_names=["python3", "python"],
        ),
        "wasm": LanguageManifest(
            name="wasm",
            version="0.18.0",
            status="sandbox",
            module="agent_harness.contracts.runtime.wasm",
            adapter_version="0.17.1",
            expected_binary_version=">= 20.0.0",
            capabilities=[
                "compute", "numeric", "crypto", "validation",
                "compile", "optimize", "wasi", "benchmark", "parallel",
            ],
            tool_count=20,
            dependencies=[],
            documentation_url="https://wasmtime.dev/",
            binary_names=["wasmtime"],
        ),
        "julia": LanguageManifest(
            name="julia",
            version="0.18.0",
            status="managed",
            module="agent_harness.contracts.runtime.julia",
            adapter_version="0.17.1",
            expected_binary_version=">= 1.9.0",
            capabilities=[
                "numeric", "linear_algebra", "ode", "optimization",
                "stats", "signal", "pde", "quantum", "ml", "nlp",
                "vision", "bio", "astro", "climate", "finance",
                "physics", "chemistry", "game", "audio", "compiler",
            ],
            tool_count=35,
            dependencies=["JSON.jl"],
            documentation_url="https://docs.julialang.org/",
            binary_names=["julia"],
        ),
        "datalog": LanguageManifest(
            name="datalog",
            version="0.18.0",
            status="managed",
            module="agent_harness.contracts.runtime.datalog",
            adapter_version="0.17.1",
            expected_binary_version=">= 5.5.0",
            capabilities=[
                "logic", "graph", "reachability", "cycle_detection",
                "scc", "shortest_path", "topo_sort", "connected_components",
                "bipartite", "mst", "typecheck", "sets", "ontology",
                "database", "security", "explain",
            ],
            tool_count=20,
            dependencies=[],
            documentation_url="https://potassco.org/clingo/",
            binary_names=["clingo"],
        ),
    }


POLYGLOT_MANIFEST: Dict[str, Dict[str, Any]] = {
    lang: manifest.to_dict()
    for lang, manifest in _build_manifest().items()
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def lang_info(lang: str) -> Dict[str, Any]:
    """Look up one language entry, return defaults if missing."""
    entry = POLYGLOT_MANIFEST.get(lang.lower().strip())
    if entry is not None:
        return entry
    return {
        "name": lang,
        "version": "0.0.0",
        "status": "unknown",
        "module": "",
        "adapter_version": "",
        "expected_binary_version": "",
        "capabilities": [],
        "tool_count": 0,
        "checksum": "",
        "dependencies": [],
        "documentation_url": "",
        "binary_names": [],
    }


def manifest_for(lang: str) -> LanguageManifest:
    """Return a :class:`LanguageManifest` for ``lang``."""
    return LanguageManifest.from_dict(lang_info(lang))


def supported_languages() -> List[str]:
    """Return all managed language names."""
    return list(POLYGLOT_MANIFEST.keys())


def capabilities_for(lang: str) -> List[str]:
    """Return the capability list for ``lang``."""
    return lang_info(lang).get("capabilities", [])


def tool_count_for(lang: str) -> int:
    """Return the advertised tool count for ``lang``."""
    return int(lang_info(lang).get("tool_count", 0))


def serialize_manifest() -> str:
    """Serialize the full manifest to JSON for machine consumers."""
    return json.dumps(POLYGLOT_MANIFEST, ensure_ascii=False, indent=2)


def load_manifest_from_json(text: str) -> Dict[str, Dict[str, Any]]:
    """Parse a JSON manifest; validates structure but does not mutate global."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    for lang, entry in data.items():
        if "name" not in entry or "module" not in entry:
            raise ValueError(f"language {lang!r} missing required fields")
    return data


# ---------------------------------------------------------------------------
# Version / compatibility checks
# ---------------------------------------------------------------------------

def is_compatible(lang: str, min_version: str) -> bool:
    """Check whether ``lang``'s adapter version >= ``min_version``."""
    entry = lang_info(lang)
    try:
        from packaging import version as _version
        return _version.parse(entry.get("adapter_version", "0.0.0")) >= _version.parse(min_version)
    except ImportError:
        return entry.get("adapter_version", "0.0.0") >= min_version


def dependency_graph() -> Dict[str, List[str]]:
    """Return language -> dependency list for the full manifest."""
    return {lang: entry.get("dependencies", []) for lang, entry in POLYGLOT_MANIFEST.items()}


# ---------------------------------------------------------------------------
# v0.18.0 release notes
# ---------------------------------------------------------------------------
RELEASE_NOTES = """
v0.18.0 Runtime Architecture Improvements
==========================================

Legacy Cleanup:
- Removed C/C++/Go/R legacy assets from assets/
- Removed v0.18.3 bloat and duplication
- Unified 5-file runtime structure

Security Hardening:
- Added SSRF prevention (validate_url)
- Added path traversal prevention (sanitize_path)
- Added JSON size limits (safe_json_dumps)
- Replaced eval() with safe math parser (AST-based)
- Added command validation (validate_command)

Test Quality:
- Added 52 new integration/security/architecture tests
- Real assertions instead of "doesn't crash"
- Error envelope shape consistency checks
- Input validation tests for all workers
- Legacy cleanup verification tests

New Capabilities:
- Julia: 35+ modes (quantum, ML, NLP, vision, bio, astro, etc.)
- Julia: Python fallbacks for all new modes
- Datalog: 8 new graph algorithms
- WASM: WAT toolchain with optimizer/typechecker/linker
- _exec: 4 new execution patterns (stream, json, batch, validate)
"""


# ---------------------------------------------------------------------------
# Release artifacts
# ---------------------------------------------------------------------------

class ReleaseArtifact:
    """Represents a release artifact (wheel, source, binary)."""

    def __init__(self, name: str, url: str, sha256: str, size_bytes: int, kind: str = "wheel"):
        self.name = name
        self.url = url
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.kind = kind

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
        }


def compute_artifact_sha256(path: Path) -> str:
    """Compute SHA256 of an artifact."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(path: Path, expected_sha256: str) -> bool:
    """Verify artifact against expected SHA256."""
    actual = compute_artifact_sha256(path)
    return actual == expected_sha256


class Changelog:
    """Maintain changelog entries."""

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    def add(self, version: str, changes: List[str], author: str = "") -> None:
        """Add a changelog entry."""
        self.entries.append({
            "version": version,
            "changes": changes,
            "author": author,
            "date": datetime.utcnow().isoformat(),
        })

    def get_latest(self) -> Dict[str, Any]:
        """Get latest changelog entry."""
        return self.entries[-1] if self.entries else {}

    def to_markdown(self) -> str:
        """Convert changelog to markdown."""
        lines = ["# Changelog", ""]
        for entry in reversed(self.entries):
            lines.append(f"## {entry['version']} - {entry['date']}")
            if entry.get("author"):
                lines.append(f"Author: {entry['author']}")
            for change in entry["changes"]:
                lines.append(f"- {change}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manifest metadata and schema evolution
# ---------------------------------------------------------------------------

class ManifestSchema:
    """Schema for manifest evolution."""

    SCHEMA_VERSIONS = {
        "1.0": {"fields": ["name", "version", "languages"], "required": ["name", "version"]},
        "2.0": {"fields": ["name", "version", "languages", "capabilities", "tool_count"], "required": ["name", "version", "languages"]},
        "3.0": {"fields": ["name", "version", "languages", "capabilities", "tool_count", "checksums", "dependencies"], "required": ["name", "version"]},
    }

    @classmethod
    def migrate(cls, manifest_dict: Dict[str, Any], from_version: str, to_version: str) -> Dict[str, Any]:
        """Migrate manifest from one schema version to another."""
        if from_version == to_version:
            return manifest_dict
        if from_version == "1.0" and to_version == "2.0":
            manifest_dict["capabilities"] = manifest_dict.get("capabilities", {})
            manifest_dict["tool_count"] = manifest_dict.get("tool_count", {})
            return manifest_dict
        if from_version == "2.0" and to_version == "3.0":
            manifest_dict["checksums"] = manifest_dict.get("checksums", {})
            manifest_dict["dependencies"] = manifest_dict.get("dependencies", {})
            return manifest_dict
        raise ValueError(f"cannot migrate from {from_version} to {to_version}")

    @classmethod
    def validate(cls, manifest_dict: Dict[str, Any], version: str = "3.0") -> List[str]:
        """Validate manifest against schema."""
        schema = cls.SCHEMA_VERSIONS.get(version, cls.SCHEMA_VERSIONS["3.0"])
        errors = []
        for field in schema["required"]:
            if field not in manifest_dict:
                errors.append(f"missing required field: {field}")
        for field in schema["fields"]:
            if field in manifest_dict and not isinstance(manifest_dict[field], (str, int, float, dict, list, bool)):
                errors.append(f"invalid type for field: {field}")
        return errors


class ManifestDiff:
    """Compute differences between two manifests."""

    @staticmethod
    def diff(old_manifest: Dict[str, Any], new_manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Compute diff between two manifests."""
        diff = {
            "added": {},
            "removed": {},
            "changed": {},
            "unchanged": {},
        }
        all_keys = set(old_manifest.keys()) | set(new_manifest.keys())
        for key in all_keys:
            if key not in old_manifest:
                diff["added"][key] = new_manifest[key]
            elif key not in new_manifest:
                diff["removed"][key] = old_manifest[key]
            elif old_manifest[key] != new_manifest[key]:
                diff["changed"][key] = {"old": old_manifest[key], "new": new_manifest[key]}
            else:
                diff["unchanged"][key] = new_manifest[key]
        return diff

    @staticmethod
    def has_breaking_changes(diff_result: Dict[str, Any]) -> bool:
        """Check if diff contains breaking changes."""
        return len(diff_result.get("removed", {})) > 0


class ManifestMerge:
    """Merge multiple manifests."""

    @staticmethod
    def merge(manifests: List[Dict[str, Any]], strategy: str = "union") -> Dict[str, Any]:
        """Merge multiple manifests."""
        if not manifests:
            return {}
        merged = manifests[0].copy()
        for manifest in manifests[1:]:
            for key, value in manifest.items():
                if strategy == "union":
                    if key not in merged:
                        merged[key] = value
                    elif isinstance(value, dict) and isinstance(merged[key], dict):
                        merged[key] = ManifestMerge.merge([merged[key], value], strategy)
                    elif isinstance(value, list) and isinstance(merged[key], list):
                        seen = set()
                        result = []
                        for item in merged[key] + value:
                            if item not in seen:
                                seen.add(item)
                                result.append(item)
                        merged[key] = result
                elif strategy == "override":
                    merged[key] = value
                elif strategy == "deep_merge":
                    if isinstance(value, dict) and isinstance(merged[key], dict):
                        merged[key] = ManifestMerge.merge([merged[key], value], strategy)
                    else:
                        merged[key] = value
        return merged


class ManifestResolver:
    """Resolve manifest dependencies and conflicts."""

    def __init__(self) -> None:
        self.resolved: Dict[str, Any] = {}
        self.conflicts: List[Dict[str, Any]] = []

    def resolve(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve manifest dependencies."""
        deps = manifest.get("dependencies", {})
        self.resolved = manifest.copy()
        for dep_name, dep_version in deps.items():
            self.resolved.setdefault("resolved_dependencies", {})[dep_name] = dep_version
        return self.resolved

    def detect_conflicts(self, manifests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect conflicts between manifests."""
        conflicts = []
        seen_versions = {}
        for manifest in manifests:
            name = manifest.get("name", "unknown")
            version = manifest.get("version", "0.0.0")
            if name in seen_versions and seen_versions[name] != version:
                conflicts.append({
                    "type": "version_conflict",
                    "name": name,
                    "versions": [seen_versions[name], version],
                })
            seen_versions[name] = version
        self.conflicts = conflicts
        return conflicts


# ---------------------------------------------------------------------------
# Module-level async fallback
# ---------------------------------------------------------------------------

async def _invoke_async_default(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "disabled", "message": "manifest async not configured", "payload": payload}


__all__ = [
    "POLYGLOT_LANGUAGES",
    "POLYGLOT_MANIFEST",
    "LanguageManifest",
    "lang_info",
    "manifest_for",
    "supported_languages",
    "capabilities_for",
    "tool_count_for",
    "serialize_manifest",
    "load_manifest_from_json",
    "is_compatible",
    "dependency_graph",
    "ReleaseArtifact",
    "Changelog",
    "ManifestSchema",
    "ManifestDiff",
    "ManifestMerge",
    "ManifestResolver",
]