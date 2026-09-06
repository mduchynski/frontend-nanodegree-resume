"""Model routing and cost accounting.

The whole cheapness strategy lives here: route the boring 90% of turns to
Haiku and only pay Sonnet prices when a turn actually needs reasoning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import cfg

# USD per 1M tokens. Cache reads are ~0.1x input, cache writes ~1.25x input.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


@dataclass(frozen=True)
class Tier:
    name: str
    model: str
    max_tokens: int

    def request_kwargs(self) -> dict:
        """Per-model params. Thinking config differs by model generation:
        Haiku 4.5 takes budget_tokens and rejects `effort`; Sonnet 5 takes
        adaptive thinking and supports `effort`.
        """
        kwargs: dict = {"model": self.model, "max_tokens": self.max_tokens}
        if self.name == "deep":
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": "medium"}
        return kwargs


FAST = Tier("fast", cfg.fast_model, 4096)
DEEP = Tier("deep", cfg.deep_model, 16000)

# Phrases that mean "actually think about this". Matching is intentionally
# generous -- a wrong escalation costs a fraction of a cent, a missed one
# costs a bad answer.
_DEEP_PATTERNS = re.compile(
    r"\b("
    r"research|analyz|analys|compare|comparison|evaluate|assess|"
    r"think through|think about|reason|figure out|work out|"
    r"pros and cons|trade[- ]?offs?|implications?|strategy|strategi|"
    r"plan (?:out|for|my)|help me (?:decide|choose|think|understand|figure)|"
    r"why (?:do|does|did|is|are|would|should)|explain|walk me through|"
    r"draft|write (?:me )?(?:a|an|the)|summar|synthesi|brainstorm|"
    r"deep dive|look into|investigate|recommend"
    r")",
    re.IGNORECASE,
)

# Short, obviously-transactional turns stay on the cheap model no matter what.
_ALWAYS_FAST = re.compile(
    r"^\s*(hi|hey|hello|thanks|thank you|yes|no|yep|nope|ok|okay|stop|cancel|"
    r"never ?mind|good morning|good night|what time|status)\b",
    re.IGNORECASE,
)


def route(user_text: str, tool_rounds: int = 0) -> Tier:
    """Pick a tier for this turn. Pure string heuristics -- no LLM call, so
    routing itself is free."""
    text = (user_text or "").strip()

    if _ALWAYS_FAST.match(text):
        return FAST
    # Multi-step tool work (research chains) benefits from the better model.
    if tool_rounds >= 3:
        return DEEP
    if _DEEP_PATTERNS.search(text):
        return DEEP
    # Long, considered input usually wants a considered answer.
    if len(text.split()) > 45:
        return DEEP
    return FAST


def cost_usd(model: str, usage) -> float:
    """Dollar cost of one API response."""
    in_rate, out_rate = PRICING.get(model, PRICING["claude-sonnet-5"])
    fresh = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        fresh * in_rate
        + cache_read * in_rate * 0.10
        + cache_write * in_rate * 1.25
        + out * out_rate
    ) / 1_000_000
