"""Per-request metrics collection — token usage, cost estimation, stage timings.

Used by the API to report query-level performance back to the frontend's
cost/latency dashboard. A `MetricsTracker` is created per request and bound to
a `contextvars.ContextVar` so the retrieval engine can attribute timings to
the current request without needing the tracker passed through the agent state.
LLM token usage is captured via a LangChain callback handler.
"""

from __future__ import annotations

import contextvars
import time
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

# Gemini 2.5 Flash pricing (USD per 1M tokens) — public list price, May 2025.
# Update when Google revises the rate card.
INPUT_PRICE_PER_1M = 0.30
OUTPUT_PRICE_PER_1M = 2.50

# Sentinel for the active per-request tracker.
current_metrics: contextvars.ContextVar[Optional["MetricsTracker"]] = (
    contextvars.ContextVar("current_metrics", default=None)
)


def _extract_usage(response: Any) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) out of a LangChain LLMResult.

    Different provider wrappers attach usage in different places; we try the
    common spots and silently return zeros if none match.
    """
    in_tok = 0
    out_tok = 0

    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage_metadata") or {}
    if isinstance(usage, dict) and usage:
        in_tok = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("input_token_count")
            or 0
        )
        out_tok = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("output_token_count")
            or 0
        )

    if not (in_tok or out_tok):
        for gens in getattr(response, "generations", []) or []:
            for gen in gens:
                msg = getattr(gen, "message", None)
                meta = getattr(msg, "usage_metadata", None) if msg else None
                if meta:
                    in_tok += meta.get("input_tokens", 0) or 0
                    out_tok += meta.get("output_tokens", 0) or 0

    return int(in_tok or 0), int(out_tok or 0)


class MetricsTracker(BaseCallbackHandler):
    """Accumulates timings and token counts for a single API request."""

    def __init__(self) -> None:
        self.tokens_input = 0
        self.tokens_output = 0
        self.llm_calls = 0
        self.llm_ms = 0.0
        self.retrieval_ms = 0.0
        self.retrieval_calls = 0
        self._llm_starts: dict[str, float] = {}
        self._t0 = time.perf_counter()

    # ── LangChain callback hooks ────────────────────────────────────────────

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        self._llm_starts[str(run_id)] = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        self._llm_starts[str(run_id)] = time.perf_counter()

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        started = self._llm_starts.pop(str(run_id), None)
        if started is not None:
            self.llm_ms += (time.perf_counter() - started) * 1000
        self.llm_calls += 1
        in_tok, out_tok = _extract_usage(response)
        self.tokens_input += in_tok
        self.tokens_output += out_tok

    # ── Retrieval engine hook ───────────────────────────────────────────────

    def add_retrieval(self, ms: float) -> None:
        self.retrieval_ms += ms
        self.retrieval_calls += 1

    # ── Reporting ───────────────────────────────────────────────────────────

    def cost_usd(self) -> float:
        return (
            self.tokens_input * INPUT_PRICE_PER_1M / 1_000_000
            + self.tokens_output * OUTPUT_PRICE_PER_1M / 1_000_000
        )

    def total_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def snapshot(self) -> dict:
        return {
            "total_ms": round(self.total_ms(), 1),
            "retrieval_ms": round(self.retrieval_ms, 1),
            "llm_ms": round(self.llm_ms, 1),
            "retrieval_calls": self.retrieval_calls,
            "llm_calls": self.llm_calls,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": round(self.cost_usd(), 6),
        }
