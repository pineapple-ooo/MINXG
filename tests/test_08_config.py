"""Centralized config: agent_harness.yaml is the source of truth."""
import pytest
import agent_harness
from agent_harness.operators import OPERATOR_REGISTRY


def test_config_loads():
    assert agent_harness.CONFIG is not None
    assert agent_harness.get("project.name") == "agent_harness"
    assert agent_harness.get("project.version")


def test_config_pillar_count():
    pillars = agent_harness.get("pillars", [])
    assert len(pillars) == 6


def test_config_operator_total_matches_registry():
    config_total = agent_harness.get("operators.total")
    assert config_total == OPERATOR_REGISTRY.total_operators


def test_config_dot_path():
    assert agent_harness.get("acceleration.c_core.functions") == 11
    assert agent_harness.get("operators.categories.ga.count") == 47


def test_config_default_on_missing_key():
    assert agent_harness.get("nonexistent.key", "default") == "default"
    assert agent_harness.get("nonexistent.key") is None
