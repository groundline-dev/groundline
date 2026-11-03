import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from groundline.core import (
    FileCache,
    Inputs,
    PipelineSpec,
    RunResult,
    StageError,
    StageSpec,
    build_pipeline,
    run_pipeline,
)
from tests.core_fakes import EXECUTED, Text, make_registry

INPUTS = Inputs(source="the source", summary="a summary")
UPPER = {"step": "upper", "implementation": "upper.basic"}
JOIN = {"step": "join", "implementation": "join.basic"}
POLISH = {"step": "polish", "implementation": "polish.basic"}


@pytest.fixture(autouse=True)
def clear_executed() -> None:
    EXECUTED.clear()


def run(
    *stages: dict[str, Any],
    cache: FileCache | None = None,
    inputs: Inputs = INPUTS,
) -> RunResult:
    spec = PipelineSpec(name="test", stages=[StageSpec(**s) for s in stages])
    pipeline = build_pipeline(spec, make_registry())
    return asyncio.run(run_pipeline(pipeline, inputs, cache=cache))


def statuses(result: RunResult) -> dict[str, str]:
    return {step: s.status for step, s in result.stages.items()}


def test_a_second_identical_run_comes_from_the_cache(tmp_path: Path):
    cache = FileCache(tmp_path)
    first = run(UPPER, JOIN, POLISH, cache=cache)
    EXECUTED.clear()

    second = run(UPPER, JOIN, POLISH, cache=cache)

    assert EXECUTED == []
    assert statuses(second) == {"upper": "cached", "join": "cached", "polish": "cached"}
    assert second.output("polish", Text) == first.output("polish", Text)


def test_cached_calls_are_replayed_and_marked_cached(tmp_path: Path):
    cache = FileCache(tmp_path)
    first = run(UPPER, cache=cache)
    second = run(UPPER, cache=cache)

    assert [c.cached for c in first.calls] == [False]
    assert [c.cached for c in second.calls] == [True]
    assert second.usage.cost_usd == 0
    assert second.usage.cached_calls == 1


def test_changing_a_setting_reruns_that_stage_and_later_ones_only(tmp_path: Path):
    cache = FileCache(tmp_path)
    run(UPPER, JOIN, POLISH, cache=cache)
    EXECUTED.clear()

    result = run(UPPER, {**JOIN, "settings": {"sep": "/"}}, POLISH, cache=cache)

    assert EXECUTED == ["join", "polish"]
    assert statuses(result)["upper"] == "cached"


def test_changing_an_input_reruns_only_stages_that_use_it(tmp_path: Path):
    cache = FileCache(tmp_path)
    run(UPPER, JOIN, cache=cache)
    EXECUTED.clear()

    # `upper` uses only the source; `join` uses the summary.
    run(UPPER, JOIN, cache=cache, inputs=Inputs(source="the source", summary="new"))

    assert EXECUTED == ["join"]


def test_switching_a_stage_off_changes_what_follows_it(tmp_path: Path):
    cache = FileCache(tmp_path)
    on = run(UPPER, JOIN, POLISH, cache=cache)
    off = run(UPPER, JOIN, {**POLISH, "enabled": False}, cache=cache)

    assert statuses(off)["polish"] == "disabled"
    assert off.output("polish", Text) != on.output("polish", Text)


def test_without_a_cache_nothing_is_stored(tmp_path: Path):
    run(UPPER, JOIN)
    run(UPPER, JOIN)

    assert EXECUTED == ["upper", "join", "upper", "join"]
    assert list(tmp_path.iterdir()) == []


def test_records_calls_and_usage():
    result = run(UPPER, JOIN)

    assert [(c.step, c.model, c.input_tokens) for c in result.calls] == [
        ("upper", "fake-model", 100)
    ]
    assert result.usage.calls == 1
    assert result.usage.unpriced_models == ["fake-model"]


def test_a_stage_cannot_read_an_input_it_did_not_declare():
    with pytest.raises(StageError, match="didn't declare it in `uses`"):
        run({"step": "upper", "implementation": "upper.peeker"})


def test_a_damaged_cache_file_is_rerun_and_rewritten(tmp_path: Path):
    cache = FileCache(tmp_path)
    run(UPPER, cache=cache)
    (entry,) = (tmp_path / "upper").iterdir()
    entry.write_text('{"output": {"te')  # cut off partway through
    EXECUTED.clear()

    second = run(UPPER, cache=cache)

    assert statuses(second) == {"upper": "ran"}
    assert EXECUTED == ["upper"]
    assert json.loads(entry.read_text())["output"] == second.output(
        "upper", Text
    ).model_dump(mode="json")
