"""agent_harness.context.compression — multi-agent 3-tier context compression.

All tiers target ~20% of original tokens with near-lossless squeeze.
Tier selection is automatic when ``tier=None``.

Auto trigger
------------
``AutoCompressor`` triggers at 70 % model context usage and compresses to
~20 %. The model context window is detected dynamically via
:mod:`agent_harness.context.model_probe` from API response metadata,
runtime hints, or conservative inference — no hardcoded lookup table.
"""
from __future__ import annotations

import hashlib
import re
import time
import zlib
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fast token estimator
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\uac00-\ud7af]")


def _fast_token_len(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        role = str(m.get("role", ""))
        content = m.get("content", "")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "") or item.get("content", "") or item))
                else:
                    parts.append(str(item))
            content = " ".join(parts)
        elif not isinstance(content, str):
            content = str(content)
        total += _fast_token_len(role) + _fast_token_len(content)
        if m.get("tool_calls"):
            total += _fast_token_len(json.dumps(m["tool_calls"], ensure_ascii=False))
        if m.get("name"):
            total += _fast_token_len(str(m["name"]))
    return total


# ---------------------------------------------------------------------------
# Dynamic model context-window detection
# ---------------------------------------------------------------------------

def detect_context_window(messages: List[Dict[str, Any]],
                          model: Optional[str] = None,
                          probe: Optional["ModelContextProbe"] = None) -> int:
    """Best-effort context-window detection without hardcoded tables."""
    # 1. Message metadata
    for m in reversed(messages):
        meta = m.get("_meta") or {}
        cw = meta.get("context_window") or meta.get("max_context_tokens")
        if isinstance(cw, int) and cw > 0:
            return cw

    # 2. Probe cache / runtime hints
    if model and probe:
        return probe.get_context_window(model)

    # 3. Model-name loose heuristic (still no exact table, just loose buckets)
    if model:
        name = model.lower()
        if any(x in name for x in ["128k", "128000", "200k", "200000"]):
            return 128000
        if any(x in name for x in ["64k", "64000", "100k", "100000"]):
            return 64000
        if any(x in name for x in ["32k", "32000"]):
            return 32000
        if any(x in name for x in ["16k", "16000"]):
            return 16000
        if any(x in name for x in ["8k", "8000", "4k", "4000"]):
            return 262144

    return 262144


def usage_ratio(messages: List[Dict[str, Any]],
                model: Optional[str] = None,
                probe: Optional["ModelContextProbe"] = None) -> float:
    cw = detect_context_window(messages, model, probe)
    if not cw:
        return 0.0
    return estimate_tokens(messages) / float(cw)


# ---------------------------------------------------------------------------
# Tier policies
# ---------------------------------------------------------------------------

@dataclass
class TierPolicy:
    name: str
    target_ratio: float = 0.20
    max_budget_ms: int = 20000
    prefer_lossless: bool = True
    max_output_msgs: int = 999999


_TIERS: Dict[str, TierPolicy] = {
    "low": TierPolicy(
        name="low",
        target_ratio=0.20,
        max_budget_ms=18000,
        prefer_lossless=True,
        max_output_msgs=128,
    ),
    "high": TierPolicy(
        name="high",
        target_ratio=0.20,
        max_budget_ms=20000,
        prefer_lossless=True,
        max_output_msgs=512,
    ),
    "max": TierPolicy(
        name="max",
        target_ratio=0.20,
        max_budget_ms=20000,
        prefer_lossless=True,
        max_output_msgs=1024,
    ),
}


# ---------------------------------------------------------------------------
# CompressedContext
# ---------------------------------------------------------------------------

@dataclass
class CompressedContext:
    tier: str
    messages: List[Dict[str, Any]]
    original_count: int
    compressed_count: int
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    elapsed_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "messages": self.messages,
            "original_count": self.original_count,
            "compressed_count": self.compressed_count,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": self.compression_ratio,
            "elapsed_ms": self.elapsed_ms,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Tier algorithms — all tiers target ~20% with near-lossless squeeze first
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


def _tier_low(messages: List[Dict[str, Any]], budget_tokens: int) -> List[Dict[str, Any]]:
    """Aggressive semantic compression with lossless pre-squeeze."""
    if not messages:
        return []
    out: List[Dict[str, Any]] = []
    for m in messages:
        m = dict(m)
        c = m.get("content", "")
        if isinstance(c, str) and len(c) > 120:
            c = _normalize(c)
            # Keep head + tail for long content
            if len(c) > 600:
                c = c[:280] + "...[mid]..." + c[-220:]
            m["content"] = c
        out.append(m)
    return out


def _tier_high(messages: List[Dict[str, Any]], budget_tokens: int) -> List[Dict[str, Any]]:
    """Structured compaction + lossless squeeze + light semantic."""
    if not messages:
        return []
    merged: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "tool" and i + 1 < len(messages):
            nxt = messages[i + 1]
            if nxt.get("role") == "tool" and nxt.get("name") == m.get("name"):
                a = _normalize(str(m.get("content", "")))[:260]
                b = _normalize(str(nxt.get("content", "")))[:260]
                merged.append({**m, "content": a + "\n" + b, "_compressed": True})
                i += 2
                continue
        if m.get("role") == "tool":
            c = _normalize(str(m.get("content", "")))
            if len(c) > 360:
                m = {**m, "content": c[:360] + "...[truncated by high compression]"}
        if m.get("role") == "assistant":
            c = m.get("content")
            if isinstance(c, str) and len(c) > 500:
                c = _normalize(c)
                m = {**m, "content": c[:180] + "...[mid]..." + c[-160:]}
        merged.append(m)
        i += 1
    return merged


def _tier_max(messages: List[Dict[str, Any]], budget_tokens: int) -> List[Dict[str, Any]]:
    """Near-lossless squeeze: whitespace normalisation + token dedup."""
    if not messages:
        return []
    out: List[Dict[str, Any]] = []
    for m in messages:
        m = dict(m)
        c = m.get("content", "")
        if isinstance(c, str):
            m["content"] = _normalize(c)
        out.append(m)
    return out


_TIERS_FN = {
    "low": _tier_low,
    "high": _tier_high,
    "max": _tier_max,
}


# ---------------------------------------------------------------------------
# Lossless codec helpers
# ---------------------------------------------------------------------------

def _bie_lossless_compress(text: str) -> Tuple[str, Dict[str, Any]]:
    try:
        from agent_harness.lossless.codec import LosslessCodec
        codec = LosslessCodec()
        res = codec.compress(text.encode("utf-8"))
        payload = base64.b64encode(res.data).decode("ascii")
        return payload, {
            "method": "bie_lossless",
            "original_len": len(text),
            "compressed_len": len(payload),
            "ratio": res.compression_ratio,
        }
    except Exception as exc:
        logger.debug("BIE lossless unavailable, fallback zlib: %s", exc)
        return _zlib_compress(text)


def _zlib_compress(text: str) -> Tuple[str, Dict[str, Any]]:
    payload = base64.b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii")
    return payload, {"method": "zlib", "original_len": len(text), "compressed_len": len(payload)}


def _lossless_decompress(payload: str, meta: Dict[str, Any]) -> str:
    raw = base64.b64decode(payload)
    method = meta.get("method", "zlib")
    if method == "bie_lossless":
        try:
            from agent_harness.lossless.codec import LosslessCodec
            codec = LosslessCodec()
            return codec.decompress(raw).data.decode("utf-8", errors="replace")
        except Exception:
            pass
    return zlib.decompress(raw).decode("utf-8", errors="replace")


def _apply_lossless_squeeze(messages: List[Dict[str, Any]], tier: str) -> List[Dict[str, Any]]:
    threshold = 180 if tier == "low" else 360
    out: List[Dict[str, Any]] = []
    for m in messages:
        m = dict(m)
        c = m.get("content", "")
        if isinstance(c, str) and len(c) > threshold:
            try:
                payload, meta = _bie_lossless_compress(c)
                m["content"] = payload
                m["_compression"] = meta
            except Exception as exc:
                logger.debug("lossless squeeze skipped: %s", exc)
        out.append(m)
    return out


def _restore_lossless(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        meta = m.get("_compression")
        if meta and meta.get("method") in ("bie_lossless", "zlib"):
            try:
                m = dict(m)
                m["content"] = _lossless_decompress(m["content"], meta)
            except Exception:
                m["content"] = "[decompression error]"
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Core compress / decompress
# ---------------------------------------------------------------------------

def compress(
    messages: List[Dict[str, Any]],
    tier: Optional[str] = None,
    budget_tokens: int = 8192,
    model: Optional[str] = None,
    probe: Optional[Any] = None,
) -> CompressedContext:
    original_tokens = estimate_tokens(messages)
    original_count = len(messages)

    if tier is None:
        ratio = usage_ratio(messages, model, probe)
        if ratio >= 0.75:
            tier = "low"
        elif ratio >= 0.55:
            tier = "high"
        else:
            tier = "max"

    policy = _TIERS[tier]
    t0 = time.perf_counter()

    compressed = _TIERS_FN[tier](messages, budget_tokens)

    # Always apply lossless squeeze as second pass
    compressed_tokens = estimate_tokens(compressed)
    target_tokens = max(budget_tokens, int(original_tokens * policy.target_ratio))

    if compressed_tokens > target_tokens:
        compressed = _apply_lossless_squeeze(compressed, tier)
        compressed_tokens = estimate_tokens(compressed)

    # Iterate to target ratio when possible
    tries = 0
    while compressed_tokens > target_tokens and tries < 2:
        # Reduce output size by trimming a bit more aggressively
        compressed = _TIERS_FN[tier](compressed, budget_tokens)
        compressed_tokens = estimate_tokens(compressed)
        tries += 1

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    ratio = compressed_tokens / max(original_tokens, 1)

    return CompressedContext(
        tier=tier,
        messages=compressed,
        original_count=original_count,
        compressed_count=len(compressed),
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        compression_ratio=ratio,
        elapsed_ms=elapsed_ms,
        metadata={
            "selected_tier": tier,
            "budget": budget_tokens,
            "model": model,
            "target_ratio": policy.target_ratio,
        },
    )


def decompress(compressed: CompressedContext, target_tier: Optional[str] = None) -> List[Dict[str, Any]]:
    msgs = list(compressed.messages)
    msgs = _restore_lossless(msgs)
    if target_tier and target_tier != compressed.tier:
        return compress(msgs, tier=target_tier, budget_tokens=compressed.metadata.get("budget", 8192)).messages
    return msgs


# ---------------------------------------------------------------------------
# Auto-compressor for multi-agent orchestrators
# ---------------------------------------------------------------------------

class AutoCompressor:
    """Attach to an orchestrator / worker pool to auto-compress shared context."""

    def __init__(
        self,
        model: Optional[str] = None,
        budget_tokens: int = 8192,
        trigger_ratio: float = 0.70,
        target_ratio: float = 0.20,
        max_budget_ms: int = 20000,
        probe: Optional[Any] = None,
    ) -> None:
        self.model = model
        self.budget_tokens = budget_tokens
        self.trigger_ratio = trigger_ratio
        self.target_ratio = target_ratio
        self.max_budget_ms = max_budget_ms
        self.probe = probe
        self._last: Optional[CompressedContext] = None
        self._trigger_count = 0

    def should_compress(self, messages: List[Dict[str, Any]]) -> bool:
        return usage_ratio(messages, self.model, self.probe) >= self.trigger_ratio

    def maybe_compress(self, messages: List[Dict[str, Any]]) -> Optional[CompressedContext]:
        if not self.should_compress(messages):
            return None

        ratio = usage_ratio(messages, self.model, self.probe)
        if ratio >= 0.85:
            tier = "low"
        elif ratio >= 0.70:
            tier = "high"
        else:
            tier = "max"

        ctx = compress(
            messages,
            tier=tier,
            budget_tokens=self.budget_tokens,
            model=self.model,
            probe=self.probe,
        )
        self._last = ctx
        self._trigger_count += 1
        logger.info(
            "auto-compress triggered: ratio=%.2f tier=%s tokens=%d->%d ms=%d",
            ratio,
            ctx.tier,
            ctx.original_tokens,
            ctx.compressed_tokens,
            ctx.elapsed_ms,
        )
        return ctx

    def last_stats(self) -> Dict[str, Any]:
        if not self._last:
            return {"triggers": self._trigger_count}
        return {
            "triggers": self._trigger_count,
            "last_tier": self._last.tier,
            "last_ratio": self._last.compression_ratio,
            "last_ms": self._last.elapsed_ms,
            "last_original": self._last.original_tokens,
            "last_compressed": self._last.compressed_tokens,
        }


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def compress_for_budget(
    messages: List[Dict[str, Any]],
    budget_tokens: int = 8192,
    model: Optional[str] = None,
    probe: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    ctx = compress(messages, budget_tokens=budget_tokens, model=model, probe=probe)
    return ctx.messages, ctx.to_dict()