import asyncio

import pytest

from groundline import (
    DEFAULT_COMMISSIONS_PRESET,
    builtin_presets,
    default_registry,
    evaluate,
    load_preset,
)
from groundline.core import PipelineSpec, with_settings
from groundline.evaluators.models import AlarmOutput
from tests.commissions_helpers import ISSUES, SUMMARY, Script
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE

PRESET = load_preset("commission_quick")


def evaluate_with(spec: PipelineSpec = PRESET):
    return asyncio.run(
        evaluate(
            SOURCE,
            SUMMARY,
            spec=spec,
            client=FakeClient(Script(commissions_check=ISSUES)),
        )
    )


def test_the_quick_preset_is_built_in():
    assert "commission_quick" in builtin_presets()
    assert DEFAULT_COMMISSIONS_PRESET == "commission_quick"
    assert PRESET.profile == "clinical"
    assert [s.step for s in PRESET.stages] == [
        "commissions_check",
        "commissions_refute",
        "alarm",
    ]


def test_the_preset_switches_refute_off():
    refute = next(s for s in PRESET.stages if s.step == "commissions_refute")

    assert refute.enabled is False


def test_the_registry_has_both_evaluators_and_one_shared_alarm_step():
    registry = default_registry()

    assert registry.steps().count("alarm") == 1
    assert registry.implementations("alarm") == [
        "alarm.critical_max",
        "alarm.critical_unsupported_max",
    ]
    assert registry.implementations("commissions_check") == [
        "commissions_check.llm_quick"
    ]
    assert registry.implementations("commissions_refute") == ["commissions_refute.llm"]


def test_an_unchanged_run_is_the_validated_preset():
    result = evaluate_with()

    assert result.validated_preset == "commission_quick"
    assert result.warnings == []
    assert result.pipeline == "commission_quick"
    assert result.profile == "clinical"
    assert result.run.output("alarm", AlarmOutput).flagged is True


def test_choosing_the_named_level_is_still_the_validated_preset():
    spec = with_settings(PRESET, "alarm", level="default")

    assert evaluate_with(spec).validated_preset == "commission_quick"


def test_changing_the_model_is_not_the_validated_preset():
    spec = with_settings(PRESET, "commissions_check", effort="high")

    result = evaluate_with(spec)

    assert result.validated_preset is None
    assert any(
        "the alarm thresholds were calibrated for commission_quick" in w
        for w in result.warnings
    )


def test_the_commission_and_omission_presets_do_not_match_each_other():
    omission = load_preset("omission_all_opus")
    assert evaluate_with().validated_preset != omission.name


def test_a_missing_key_is_reported_before_any_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
):
    from groundline.settings import MissingAPIKeyError

    monkeypatch.chdir(str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(MissingAPIKeyError, match="commissions_check"):
        asyncio.run(evaluate(SOURCE, SUMMARY, spec=PRESET))


@pytest.mark.parametrize("order", ["omissions first", "commissions first", "alone"])
def test_evaluators_can_be_registered_in_any_order_or_alone(order: str):
    from groundline.core import Registry
    from groundline.evaluators.commissions import register_commissions
    from groundline.evaluators.omissions import register_omissions

    registries = {
        "omissions first": [register_omissions, register_commissions],
        "commissions first": [register_commissions, register_omissions],
        "alone": [register_commissions],
    }[order]
    registry = Registry()
    for register in registries:
        register(registry)

    assert registry.steps().count("alarm") == 1
    assert "alarm.critical_unsupported_max" in registry.implementations("alarm")
