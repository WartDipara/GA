"""Versioned OpenAI API reference estimates; never represent subscription or invoice charges."""

import tomllib
from decimal import Decimal


def reference_cost(root, model, usage):
    result = {
        "estimate_usd": None,
        "source": None,
        "price_version": None,
        "scenario": None,
        "basis": "OpenAI API reference estimate; not actual account billing",
        "reason": None,
    }
    try:
        book = tomllib.loads((root / "config/pricing.toml").read_text(encoding="utf-8"))
        rates = book["models"].get(model)
        result.update(
            source=book["source"], price_version=book["version"], scenario=book["scenario"]
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result["reason"] = "Optional pricing reference is unavailable"
        return result
    keys = ("inputTokens", "outputTokens", "cachedInputTokens", "cacheWriteInputTokens")
    if rates is None:
        result["reason"] = "No verified rate for the exact reported model"
        return result
    if usage is None or any(type(usage.get(key)) is not int for key in keys):
        result["reason"] = "Input, output, cache-read or cache-write usage is unavailable"
        return result
    uncached = usage["inputTokens"] - usage["cachedInputTokens"] - usage["cacheWriteInputTokens"]
    if min(uncached, *(usage[key] for key in keys)) < 0:
        result["reason"] = "Inconsistent provider token counters"
        return result
    components = (
        (uncached, "input_per_million"),
        (usage["cachedInputTokens"], "cached_input_per_million"),
        (usage["cacheWriteInputTokens"], "cache_write_per_million"),
        (usage["outputTokens"], "output_per_million"),
    )
    try:
        amount = sum(Decimal(count) * Decimal(str(rates[key])) for count, key in components)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        result["reason"] = "Optional pricing rates are invalid"
        return result
    result["estimate_usd"] = float(amount / Decimal(1_000_000))
    return result
