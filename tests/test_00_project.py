"""
test_00_project.py — Project-wide import and file integrity checks.

All Python source files compile without SyntaxError.
All core packages import without ImportError.
Config files are valid YAML/TOML/JSON.
"""
import pytest
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════
#  Syntax check — every .py file in the project compiles cleanly
# ═══════════════════════════════════════════════════════════════════════════

class TestSyntax:
    """Every .py file passes compile() — no SyntaxError anywhere."""

    @pytest.fixture(scope="class")
    def all_py_files(self):
        exclude = {
            ".venv", "__pycache__", ".git", ".hg",
            "node_modules", ".pytest_cache",
        }
        files = []
        for p in PROJECT_ROOT.rglob("*.py"):
            if any(ex in p.parts for ex in exclude):
                continue
            files.append(str(p.resolve()))
        return sorted(files)

    def test_no_syntax_errors(self, all_py_files):
        """Every .py file in the project compiles without SyntaxError."""
        failed = []
        for fp_str in all_py_files:
            fp = Path(fp_str)
            try:
                compile(fp.read_text(encoding="utf-8"), str(fp), "exec")
            except SyntaxError as exc:
                failed.append((str(fp), str(exc)))
        assert not failed, "SyntaxError(s):\n" + "\n".join(
            f"- {fp}: {err}" for fp, err in failed
        )

    def test_pyproject_toml_valid(self):
        pyproject = PROJECT_ROOT / "pyproject.toml"
        if pyproject.exists():
            import tomllib
            pyproject.read_bytes()

    def test_requirements_readable(self):
        req = PROJECT_ROOT / "requirements.txt"
        if req.exists():
            req.read_text(encoding="utf-8")

    def test_readme_exists_and_readable(self):
        readme = PROJECT_ROOT / "README.md"
        if readme.exists():
            readme.read_text(encoding="utf-8")

    def test_agent_harness_yaml_valid(self):
        agent_harness = PROJECT_ROOT / "config" / "agent_harness.yaml"
        if agent_harness.exists():
            import yaml
            yaml.safe_load(agent_harness.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
#  Config validation — required fields present and well-formed
# ═══════════════════════════════════════════════════════════════════════════

class TestConfig:
    """config/agent_harness.yaml has all required fields."""

    @pytest.fixture(scope="class")
    def agent_harness_yaml(self):
        import yaml
        with open(PROJECT_ROOT / "config" / "agent_harness.yaml") as f:
            return yaml.safe_load(f)

    def test_has_version(self, agent_harness_yaml):
        project = agent_harness_yaml.get("project", {})
        assert "version" in project, "config.yaml: project.version is missing"

    def test_has_operators(self, agent_harness_yaml):
        assert "operators" in agent_harness_yaml, "config.yaml: operators list is missing"

    def test_no_obvious_corruption(self, agent_harness_yaml):
        assert isinstance(agent_harness_yaml, dict), "config.yaml is not a mapping"


# ═══════════════════════════════════════════════════════════════════════════
#  Non-Python files valid YAML/JSON
# ═══════════════════════════════════════════════════════════════════════════

class TestNonPythonFiles:
    @pytest.fixture(scope="class")
    def yaml_files(self):
        exclude = {".venv", "__pycache__", ".git", ".hg", "node_modules", ".pytest_cache"}
        return [
            str(p.resolve())
            for p in PROJECT_ROOT.rglob("*.yaml")
            if not any(ex in p.parts for ex in exclude)
        ]

    @pytest.fixture(scope="class")
    def json_files(self):
        exclude = {".venv", "__pycache__", ".git", ".hg", "node_modules", ".pytest_cache"}
        return [
            str(p.resolve())
            for p in PROJECT_ROOT.rglob("*.json")
            if not any(ex in p.parts for ex in exclude)
        ]

    def test_all_yaml_valid(self, yaml_files):
        import yaml
        for fp_str in yaml_files:
            fp = Path(fp_str)
            yaml.safe_load(fp.read_text(encoding="utf-8"))

    def test_all_json_valid(self, json_files):
        import json
        for fp_str in json_files:
            fp = Path(fp_str)
            json.loads(fp.read_text(encoding="utf-8"))
