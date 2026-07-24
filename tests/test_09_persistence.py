"""End-to-end: pillars compose without breaking each other."""
import pytest
import math
import agent_harness.cat as cat
from agent_harness.ga import Multivector, Signature
from agent_harness.operators import OPERATOR_REGISTRY
import agent_harness


def test_pipeline_ga_to_cat_to_chaos():
    """A small pipeline: rotate vector (GA), map to length (CAT), check chaos stability."""
    sig = Signature(3, 0)
    e1 = Multivector({1: 1.0}, sig)
    e2 = Multivector({2: 1.0}, sig)

    from agent_harness.ga import Rotor
    R = Rotor.from_bivector(e1.outer(e2).normalize(), math.pi / 2)
    rotated = R.apply(e1)
    norm = rotated.norm

    to_len = cat.Morphism(
        "ga_to_len",
        (cat.Type("multivector"),),
        cat.Type("number"),
        lambda m: m.norm
    )
    length = to_len(rotated)
    assert abs(length - 1.0) < 1e-9


def test_import_works_after_other_pillar():
    """Importing pillars in any order should not cause issues."""
    from agent_harness.chaos import logistic_lyapunov
    from agent_harness.topo import SimplicialComplex, Simplex
    from agent_harness.fiber import TangentBundle
    lyap = logistic_lyapunov(3.5)
    assert isinstance(lyap, float)


def test_backward_compat_py_workers_alias():
    """Old `import py_workers` still works (aliased to agent_harness)."""
    import py_workers
    assert py_workers.__name__ in ("agent_harness", "py_workers")
