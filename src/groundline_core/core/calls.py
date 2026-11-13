"""Records of model calls, and what they cost.

Every model call a stage makes is recorded: which model answered, tokens, and
time. Calls replayed from the cache are marked `cached` and cost nothing.
"""

from collections.abc import Iterable

from pydantic import BaseModel


class Price(BaseModel):
    """US dollars per million tokens."""

    input: float
    output: float


PRICES: dict[str, Price] = {
    # Anthropic first-party API rates.
    "claude-opus-5-5": Price(input=4.0, output=20.0),
    "claude-sonnet-5-5": Price(input=2.0, output=10.0),
    "claude-haiku-5-5": Price(input=0.1, output=0.5),
}
"""Models without an entry are reported as unpriced rather than guessed at."""


class CallRecord(BaseModel):
    step: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    cached: bool = False


class Usage(BaseModel):
    calls: int
    cached_calls: int
    input_tokens: int
    """Live (not cached) calls only, like the other token and cost totals."""
    output_tokens: int
    cost_usd: float
    unpriced_models: list[str]
    """Models that made live calls but have no price, so aren't in `cost_usd`."""


def summarize_usage(
    calls: Iterable[CallRecord], prices: dict[str, Price] = PRICES
) -> Usage:
    calls = list(calls)
    live = [c for c in calls if not c.cached]
    cost = 0.0
    unpriced: set[str] = set()
    for c in live:
        price = prices.get(c.model)
        if price is None:
            unpriced.add(c.model)
            continue
        cost += (c.input_tokens * price.input + c.output_tokens * price.output) / 1e6
    return Usage(
        calls=len(calls),
        cached_calls=len(calls) - len(live),
        input_tokens=sum(c.input_tokens for c in live),
        output_tokens=sum(c.output_tokens for c in live),
        cost_usd=cost,
        unpriced_models=sorted(unpriced),
    )
