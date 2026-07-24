"""Thin wrapper: Julia adapter delegated to ``agent_harness.contracts.runtime.scientific``."""
from __future__ import annotations

from .scientific import (  # noqa: F401
    ADAPTER_NAME as _SCIENTIFIC_NAME,
    ADAPTER_VERSION as _SCIENTIFIC_VERSION,
    ADAPTER_STATUS as _SCIENTIFIC_STATUS,
    handle_julia as handle,
    invoke_julia as invoke,
)

ADAPTER_NAME = "julia"
ADAPTER_VERSION = _SCIENTIFIC_VERSION
ADAPTER_STATUS = _SCIENTIFIC_STATUS

__all__ = ["ADAPTER_NAME", "ADAPTER_VERSION", "ADAPTER_STATUS", "handle", "invoke"]
