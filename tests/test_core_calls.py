import pytest

from groundline.core import CallRecord, summarize_usage


def call(model: str, input_tokens: int, output_tokens: int, cached: bool = False):
    return CallRecord(
        step="extract",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        seconds=1.0,
        cached=cached,
    )


def test_costs_calls_at_the_model_price():
    # Claude Opus 5.5: $4 per million input tokens, $20 per million output tokens.
    usage = summarize_usage([call("claude-opus-5-5", 1_000_000, 1_000_000)])

    assert usage.cost_usd == pytest.approx(24.0)


def test_cached_calls_are_counted_but_cost_nothing():
    usage = summarize_usage(
        [
            call("claude-opus-5-5", 1000, 100),
            call("claude-opus-5-5", 5000, 500, cached=True),
        ]
    )

    assert usage.calls == 2
    assert usage.cached_calls == 1
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 100
    assert usage.cost_usd == pytest.approx((1000 * 4 + 100 * 20) / 1_000_000)


def test_models_without_a_price_are_reported_not_guessed():
    usage = summarize_usage(
        [call("claude-opus-5-5", 1_000_000, 0), call("mystery-model", 10, 10)]
    )

    assert usage.cost_usd == pytest.approx(4.0)
    assert usage.unpriced_models == ["mystery-model"]
