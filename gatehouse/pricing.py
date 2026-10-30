"""List prices in USD per million tokens, used to price dry runs and per-step usage.

In managed mode the session's `usage.list_cost` reported by the platform is the authoritative
run total; these figures only split it by step. Update when prices change.
"""

from __future__ import annotations

PRICES_PER_MTOK = {
    # model: (input, output, cache_read, cache_write_5m)
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def cost_usd(model: str, tokens: dict) -> float:
    inp, out, cread, cwrite = PRICES_PER_MTOK[model]
    total = (
        tokens.get("input", 0) * inp
        + tokens.get("output", 0) * out
        + tokens.get("cache_read", 0) * cread
        + tokens.get("cache_write", 0) * cwrite
    ) / 1_000_000
    return round(total, 6)


def add_tokens(a: dict, b: dict) -> dict:
    keys = ("input", "output", "cache_read", "cache_write")
    return {k: a.get(k, 0) + b.get(k, 0) for k in keys}
