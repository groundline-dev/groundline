import asyncio
import time
from typing import Any

import pytest

from groundline_core.core import (
    Inputs,
    Pipeline,
    PipelineSpec,
    RunResult,
    SetupError,
    StageError,
    StageSpec,
    build_pipeline,
    run_pipeline,
)
from tests.core_fakes import Text, WrongType, make_registry

INPUTS = Inputs(source="the source", summary="a summary")


def pipeline(*stages: dict[str, Any]) -> Pipeline:
    registry = make_registry()
    registry.register(WrongType)
    pipeline_spec = PipelineSpec(name="test", stages=[StageSpec(**s) for s in stages])
    return build_pipeline(pipeline_spec, registry)


def run(p: Pipeline) -> RunResult:
    return asyncio.run(run_pipeline(p, INPUTS))


UPPER = {"step": "upper", "implementation": "upper.basic"}
JOIN = {"step": "join", "implementation": "join.basic"}
POLISH = {"step": "polish", "implementation": "polish.basic"}


def test_runs_stages_and_passes_outputs_along():
    result = run(pipeline(UPPER, JOIN, POLISH))

    assert result.output("polish", Text).value == "THE SOURCE+a summary!"
    assert {step: s.status for step, s in result.stages.items()} == {
        "upper": "ran",
        "join": "ran",
        "polish": "ran",
    }


def test_asking_for_a_step_the_pipeline_lacks_names_it():
    result = run(pipeline(UPPER))

    with pytest.raises(LookupError, match="the pipeline has no 'polish' stage"):
        result.output("polish", Text)


def test_a_switched_off_stage_passes_its_input_through():
    result = run(pipeline(UPPER, JOIN, {**POLISH, "enabled": False}))

    assert result.output("polish", Text).value == "THE SOURCE+a summary"
    assert result.stages["polish"].status == "disabled"


def test_independent_stages_run_in_parallel():
    started = time.perf_counter()
    result = run(
        pipeline(
            {"step": "left", "implementation": "left.slow"},
            {"step": "right", "implementation": "right.slow"},
            {"step": "both", "implementation": "both.basic"},
        )
    )
    elapsed = time.perf_counter() - started

    assert result.output("both", Text).value == "left+right"
    assert elapsed < 0.5  # one after the other would take at least 0.6 s


def test_a_failing_stage_stops_the_run_and_names_the_step():
    p = pipeline(
        {"step": "left", "implementation": "left.boom"},
        {"step": "right", "implementation": "right.slow", "settings": {"delay": 5}},
    )

    started = time.perf_counter()
    with pytest.raises(StageError) as error:
        run(p)

    assert error.value.step == "left"
    assert "model unavailable" in str(error.value)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert time.perf_counter() - started < 1  # the slow stage was canceled


def test_a_failed_run_still_reports_the_calls_it_paid_for():
    p = pipeline(UPPER, {"step": "left", "implementation": "left.paid_boom"})

    with pytest.raises(StageError) as error:
        run(p)

    assert [(c.step, c.input_tokens) for c in error.value.calls] == [
        ("upper", 100),
        ("left", 50),
    ]


def test_setup_errors_are_not_reported_as_a_stage_failure():
    with pytest.raises(SetupError, match="key was rejected"):
        run(pipeline({"step": "left", "implementation": "left.setup"}))


def test_a_stage_cannot_read_an_output_it_did_not_declare():
    with pytest.raises(StageError, match="didn't declare it in `reads`"):
        run(pipeline(UPPER, {"step": "join", "implementation": "join.sneaky"}))


def test_a_stage_must_return_its_step_output_type():
    with pytest.raises(StageError, match="returned WrongOutput, expected Text"):
        run(pipeline({"step": "upper", "implementation": "upper.wrong"}))


def test_reading_an_output_as_the_wrong_type_is_an_error():
    result = run(pipeline(UPPER))

    with pytest.raises(TypeError):
        result.output("upper", Inputs)
