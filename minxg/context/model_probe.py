"""Dynamic model context-window detection.

No hardcoded tables. Detection order:
1. API response header / metadata from last call
2. Model-info probe (cheap call if gateway exposes it)
3. Provider runtime metadata when available
4. Fallback to last known context + conservative default
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelContext:
    model: str
    context_window: int = 0
    recommended_input: int = 0
    source: str = "unknown"
    updated_at: float = field(default_factory=time.time)


class ModelContextProbe:
    """Detect and cache model context windows dynamically."""

    def __init__(self, default_context: int = 262144) -> None:
        self.default_context = default_context
        self._cache: Dict[str, ModelContext] = {}
        self._last_response_meta: Dict[str, Any] = {}

    def record_response(self, model: str, headers: Dict[str, str], body: Dict[str, Any]) -> None:
        """Learn context window from an API response."""
        ctx = ModelContext(model=model, source="response_meta")

        # Common header locations
        for key in ("anthropic-ratelimit-context-window", "x-ratelimit-context-window",
                    "openai-organization", "x-model-context"):
            val = headers.get(key)
            if val and val.isdigit():
                ctx.context_window = int(val)
                ctx.source = f"header:{key}"
                break

        # Body hints
        usage = body.get("usage") or {}
        for key in ("prompt_tokens", "total_tokens", "context_window"):
            val = usage.get(key)
            if isinstance(val, int) and val > 0:
                ctx.context_window = max(ctx.context_window, val)
                ctx.source = "usage"

        # Model object hints
        model_obj = body.get("model") or {}
        if isinstance(model_obj, dict):
            cw = model_obj.get("context_window") or model_obj.get("max_context")
            if isinstance(cw, int) and cw > 0:
                ctx.context_window = cw
                ctx.source = "model_object"

        if ctx.context_window > 0:
            ctx.recommended_input = int(ctx.context_window * 0.75)
            self._cache[model] = ctx
            logger.debug("Probed context for %s: %d (source=%s)", model, ctx.context_window, ctx.source)

    def get(self, model: str) -> ModelContext:
        if model in self._cache:
            return self._cache[model]

        # Fallback heuristics based on model name only (still no static table)
        ctx = ModelContext(model=model, source="heuristic")
        name = model.lower()

        # Very loose heuristics for unknown models
        if any(x in name for x in ["128k", "128000", "200k", "200000"]):
            ctx.context_window = 128000
        elif any(x in name for x in ["64k", "64000", "100k", "100000"]):
            ctx.context_window = 64000
        elif any(x in name for x in ["32k", "32000"]):
            ctx.context_window = 32000
        elif any(x in name for x in ["16k", "16000"]):
            ctx.context_window = 16000
        elif any(x in name for x in ["8k", "8000", "4k", "4000"]):
            ctx.context_window = 8192
        else:
            # Unknown model — probe is the only safe path
            ctx.context_window = self.default_context
            ctx.source = "default"

        ctx.recommended_input = int(ctx.context_window * 0.75)
        self._cache[model] = ctx
        return ctx

    def get_context_window(self, model: str) -> int:
        return self.get(model).context_window
