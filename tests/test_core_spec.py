from typing import Any

import pytest
from pydantic import ValidationError

from groundline.core import PipelineSpec, SpecError, StageSpec, build_pipeline
from tests.core_fakes import Join, make_registry


def spec(*stages: dict[str, Any]) -> PipelineSpec:
    return PipelineSpec(name="test", stages=[StageSpec(**s) for s in stages])


UPPER = {"step": "upper", "implementation": "upper.basic"}
JOIN = {"step": "join", "implementation": "join.basic"}


def problems(pipeline_spec: PipelineSpec) -> list[str]:
    with pytest.raises(SpecError) as error:
        build_pipeline(pipeline_spec, make_registry())
    return error.value.problems


def test_builds_a_valid_pipeline_with_its_settings():
    pipeline = build_pipeline(
        spec(UPPER, {**JOIN, "settings": {"sep": " / "}}), make_registry()
    )

    assert [s.step for s in pipeline.stages] == ["upper", "join"]
    join = pipeline.stages[1].implementation
    assert isinstance(join, Join)
    assert join.sep == " / "


def test_settings_default_when_not_given():
    pipeline = build_pipeline(spec(UPPER, JOIN), make_registry())

    join = pipeline.stages[1].implementation
    assert isinstance(join, Join)
    assert join.sep == "+"


def test_rejects_an_unknown_implementation():
    assert problems(spec({"step": "upper", "implementation": "upper.nope"})) == [
        "stage 1 (upper): unknown implementation 'upper.nope'"
    ]


def test_rejects_an_implementation_of_a_different_step():
    assert problems(spec({"step": "upper", "implementation": "join.basic"})) == [
        "stage 1 (upper): 'join.basic' implements step 'join', not 'upper'"
    ]


def test_rejects_a_step_used_twice():
    assert problems(spec(UPPER, UPPER)) == [
        "stage 2 (upper): step 'upper' appears more than once"
    ]


def test_rejects_invalid_and_unknown_settings():
    found = problems(spec(UPPER, {**JOIN, "settings": {"sep": 3, "color": "red"}}))

    assert found == [
        "stage 2 (join): setting 'sep': Input should be a valid string",
        "stage 2 (join): setting 'color': Extra inputs are not permitted",
    ]


def test_rejects_reading_a_step_that_is_missing_or_comes_later():
    assert problems(spec(JOIN)) == [
        "stage 1 (join): reads 'upper', which must come earlier in the pipeline"
    ]
    assert problems(spec(JOIN, UPPER)) == [
        "stage 1 (join): reads 'upper', which must come earlier in the pipeline"
    ]


def test_rejects_switching_off_a_stage_that_cannot_be_switched_off():
    assert problems(spec({**UPPER, "enabled": False})) == [
        "stage 1 (upper): 'upper.basic' can't be switched off"
    ]


def test_reports_every_problem_at_once():
    found = problems(
        spec({"step": "upper", "implementation": "upper.nope"}, JOIN, JOIN)
    )

    assert len(found) == 2
    assert "unknown implementation" in found[0]
    assert "appears more than once" in found[1]


def test_spec_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate({"name": "x", "stages": [], "color": "red"})
    with pytest.raises(ValidationError):
        StageSpec.model_validate({**UPPER, "enable": False})
