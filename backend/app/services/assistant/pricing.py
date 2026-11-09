"""Token prices for cost ESTIMATES (Prompt 11 Part B, B4).

USD per 1M tokens: (input, cached input, output). Standard (non-batch),
short-context rates from OpenAI's pricing page
(https://developers.openai.com/api/docs/pricing), read 2026-11-08. Prices
change; `ASSISTANT_PRICES` overrides any row without a code change. An
estimate is only as current as this table - the bill is the source of truth.
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
}


def price_for(model: str, overrides: dict | None = None) -> tuple[float, float, float] | None:
    table = {**PRICES, **{k: tuple(v) for k, v in (overrides or {}).items()}}
    if model in table:
        return table[model]
    # Dated snapshots ("gpt-5.6-luna-2026-07-15") bill at their family's rate.
    for name in sorted(table, key=len, reverse=True):
        if model.startswith(name):
            return table[name]
    return None


def cost_usd(by_model: dict[str, list[int]], overrides: dict | None = None,
             alias: dict[str, str] | None = None) -> float | None:
    """by_model: model -> [input (incl. cached), cached input, output]. `alias`
    prices a stand-in (the fake provider's tiers) as a real model."""
    total = 0.0
    for model, (inp, cached, out) in by_model.items():
        price = price_for((alias or {}).get(model, model), overrides)
        if price is None:
            return None
        p_in, p_cached, p_out = price
        total += ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
    return total


__all__ = ["PRICES", "cost_usd", "price_for"]
