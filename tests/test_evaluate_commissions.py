import asyncio

import pytest

from groundline_core import (
    DEFAULT_COMMISSIONS_PRESET,
    builtin_presets,
    default_registry,
    evaluate,
    load_preset,
)
from groundline_core.core import PipelineSpec, SpecError, with_settings
from groundline_core.evaluators.models import AlarmOutput
from tests.commissions_helpers import (
    ISSUES,
    SUMMARY,
    Script,
    lean_script,
)
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
    assert registry.implementations("commissions_claims") == ["commissions_claims.llm"]
    assert registry.implementations("commissions_check") == [
        "commissions_check.llm_quick",
        "commissions_check.llm_support",
    ]
    assert registry.implementations("commissions_refute") == [
        "commissions_refute.llm",
        "commissions_refute.llm_flagged",
    ]


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
    from groundline_core.settings import MissingAPIKeyError

    monkeypatch.chdir(str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(MissingAPIKeyError, match="commissions_check"):
        asyncio.run(evaluate(SOURCE, SUMMARY, spec=PRESET))


@pytest.mark.parametrize("order", ["omissions first", "commissions first", "alone"])
def test_evaluators_can_be_registered_in_any_order_or_alone(order: str):
    from groundline_core.core import Registry
    from groundline_core.evaluators.commissions import register_commissions
    from groundline_core.evaluators.omissions import register_omissions

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


# --- the lean preset -------------------------------------------------------------

LEAN_PRESET = load_preset("commission_reverse_lean")


def evaluate_lean(spec: PipelineSpec = LEAN_PRESET):
    client = FakeClient(lean_script())
    return asyncio.run(evaluate(SOURCE, SUMMARY, spec=spec, client=client))


def test_the_lean_preset_is_built_in_with_its_three_stages_and_alarm():
    assert "commission_reverse_lean" in builtin_presets()
    assert LEAN_PRESET.profile == "clinical"
    assert [(s.step, s.implementation, s.enabled) for s in LEAN_PRESET.stages] == [
        ("commissions_claims", "commissions_claims.llm", True),
        ("commissions_check", "commissions_check.llm_support", True),
        ("commissions_refute", "commissions_refute.llm_flagged", True),
        ("alarm", "alarm.critical_unsupported_max", True),
    ]


def test_the_lean_preset_uses_only_opus():
    models = [s.settings["model"] for s in LEAN_PRESET.stages if "model" in s.settings]

    assert models == ["claude-opus-5-5"] * 3


def test_the_lean_preset_has_its_own_threshold_not_quicks():
    alarm = next(s for s in LEAN_PRESET.stages if s.step == "alarm")

    assert alarm.settings == {"thresholds": {"default": 0.80}, "level": "default"}


def test_an_unchanged_lean_run_is_its_validated_preset():
    result = evaluate_lean()

    assert result.validated_preset == "commission_reverse_lean"
    assert result.warnings == []
    alarm = result.run.output("alarm", AlarmOutput)
    assert (alarm.score, alarm.threshold, alarm.flagged) == (0.95, 0.80, True)


def test_lean_refute_after_the_quick_check_is_rejected_before_any_call():
    quick_check = next(s for s in PRESET.stages if s.step == "commissions_check")
    stages = [
        quick_check if s.step == "commissions_check" else s for s in LEAN_PRESET.stages
    ]
    spec = LEAN_PRESET.model_copy(update={"name": "my_lean", "stages": stages})

    with pytest.raises(SpecError) as error:
        asyncio.run(evaluate(SOURCE, SUMMARY, spec=spec, client=FakeClient([])))

    assert error.value.problems == [
        "stage 3 (commissions_refute): 'commissions_refute.llm_flagged' needs "
        "commissions_check to be 'commissions_check.llm_support', not "
        "'commissions_check.llm_quick'"
    ]


def test_the_two_commission_presets_do_not_match_each_other():
    assert evaluate_lean().validated_preset != PRESET.name
    assert evaluate_with().validated_preset != LEAN_PRESET.name
