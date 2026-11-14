"""The JSON schema editors use to check pipeline specs, such as presets."""

import json
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from groundline_core.presets import BUILTIN_DIR, builtin_presets
from groundline_core.presets.schema import SCHEMA_PATH, spec_schema


def preset(name: str = "omission_all_opus") -> dict[str, Any]:
    return yaml.safe_load((BUILTIN_DIR / f"{name}.yaml").read_text())


def problems(spec: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(spec_schema())
    # The stubs leave one of iter_errors' overloads partly untyped.
    errors = validator.iter_errors(spec)  # pyright: ignore[reportUnknownMemberType]
    return [error.message for error in errors]


def with_stage(step: str, **changes: Any) -> dict[str, Any]:
    spec = preset()
    stage = next(s for s in spec["stages"] if s["step"] == step)
    stage.update(changes)
    return spec


def test_the_committed_schema_is_up_to_date():
    committed = json.loads(SCHEMA_PATH.read_text())

    assert committed == spec_schema(), (
        "pipeline.schema.json is out of date: run "
        "`uv run python -m groundline_core.presets.schema`"
    )


def test_the_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(spec_schema())


@pytest.mark.parametrize("name", builtin_presets())
def test_every_built_in_preset_passes_and_points_editors_at_the_schema(name: str):
    path = BUILTIN_DIR / f"{name}.yaml"

    assert problems(preset(name)) == []
    assert path.read_text().splitlines()[0] == (
        "# yaml-language-server: $schema=pipeline.schema.json"
    )


def test_a_misspelled_setting_is_caught():
    spec = with_stage("check", settings={"model": "claude-opus-5-5", "efort": "medium"})

    assert any("'efort'" in p for p in problems(spec))


def test_a_setting_value_is_checked_against_the_implementation():
    spec = with_stage("check", settings={"effort": "extreme"})

    assert any("'extreme'" in p for p in problems(spec))


def test_an_unknown_implementation_is_caught():
    assert any(
        "'check.nope'" in p
        for p in problems(with_stage("check", implementation="check.nope"))
    )


def test_an_implementation_must_match_its_step():
    spec = with_stage("check", implementation="refute.llm")

    assert any("'refute'" in p for p in problems(spec))


def test_only_stages_that_can_be_switched_off_may_be():
    assert problems(with_stage("refute", enabled=False)) == []
    assert problems(with_stage("check", enabled=False)) != []


def test_unknown_fields_are_caught():
    assert problems({**preset(), "stagez": []}) != []
    assert problems(with_stage("check", setings={})) != []
