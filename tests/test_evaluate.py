import asyncio
from pathlib import Path

import pytest

from groundline_core import (
    EvaluationResult,
    builtin_presets,
    evaluate,
    evaluate_sync,
    load_preset,
)
from groundline_core.core import (
    DomainProfile,
    PipelineSpec,
    SpecError,
    StageSpec,
    load_spec,
    with_settings,
)
from groundline_core.core.deadline import check_deadline
from groundline_core.evaluators.models import AlarmOutput
from groundline_core.evaluators.omissions.models import ScoreOutput
from groundline_core.profiles import load_builtin_profile
from groundline_core.settings import MissingAPIKeyError, UnreadableSettingsError
from tests.core_fakes import make_registry
from tests.fakes import FakeClient
from tests.omission_helpers import (
    EXTRACTED,
    RANKED,
    SOURCE,
    SUMMARY,
    Script,
    check_reply,
    flagged_script,
    refute_reply,
)

PRESET = load_preset("omission_all_opus")


def script() -> Script:
    return Script(
        extract=EXTRACTED,
        rank=RANKED,
        check=check_reply(),
        refute=refute_reply(("f1", "no", None), ("f2", "no", None)),
    )


def run(
    spec: PipelineSpec = PRESET, profile: DomainProfile | None = None
) -> EvaluationResult:
    return asyncio.run(
        evaluate(
            SOURCE, SUMMARY, spec=spec, profile=profile, client=FakeClient(script())
        )
    )


# --- the preset ---------------------------------------------------------------


def test_the_all_opus_preset_is_built_in():
    assert "omission_all_opus" in builtin_presets()
    assert PRESET.profile == "clinical"
    assert [s.step for s in PRESET.stages] == [
        "extract",
        "rank",
        "check",
        "refute",
        "score",
        "alarm",
    ]


def test_an_unknown_preset_is_an_error():
    with pytest.raises(ValueError, match="no built-in preset 'fastest'"):
        load_preset("fastest")


# --- evaluate -----------------------------------------------------------------


def test_runs_the_preset_by_default():
    result = asyncio.run(evaluate(SOURCE, SUMMARY, client=FakeClient(script())))

    assert result.pipeline == "omission_all_opus"
    assert result.profile == "clinical"
    assert result.run.output("score", ScoreOutput).total == 3
    assert result.run.output("alarm", AlarmOutput).flagged is False
    assert [c.step for c in result.run.calls] == ["extract", "rank", "check", "refute"]


def test_the_result_reports_the_omission_check_without_stage_outputs():
    result = asyncio.run(evaluate(SOURCE, SUMMARY, client=FakeClient(flagged_script())))

    assert result.evaluation_type == "omissions"
    assert result.flagged is True
    assert result.score == pytest.approx(0.25)
    assert result.alarm.model_dump() == {
        "score": 0.95,
        "threshold": 0.9,
        "level": "strict",
    }
    assert result.counts is not None
    assert result.counts.model_dump() == {
        "total": 3,
        "present": 0,
        "partial": 1,
        "absent": 2,
    }
    assert [(f.type, f.verdict, f.statement) for f in result.findings] == [
        ("omission", "absent", "Motrin 600 mg every 6 hours for a week."),
        ("omission", "partial", "Injured the finger skiing."),
        ("omission", "absent", "Pain is worse with movement."),
    ]
    run_usage = result.run.usage
    assert result.usage.model_dump() == {
        "calls": 4,
        "input_tokens": run_usage.input_tokens,
        "output_tokens": run_usage.output_tokens,
        "cost_usd": run_usage.cost_usd,
    }


@pytest.mark.parametrize(
    "case, expected",
    [
        ("without its alarm", "alarm"),
        ("with both checks' steps", "both checks"),
        ("with no check's steps", "alarm"),
    ],
)
def test_a_spec_that_is_not_one_full_check_fails_before_the_key_lookup(
    case: str, expected: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # With no key anywhere, reaching the key lookup would raise MissingAPIKeyError.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    registry = None
    if case == "without its alarm":
        spec = PRESET.model_copy(
            update={"stages": [s for s in PRESET.stages if s.step != "alarm"]}
        )
    elif case == "with both checks' steps":
        omissions = [s for s in PRESET.stages if s.step != "alarm"]
        commissions = load_preset("commission_quick").stages
        spec = PRESET.model_copy(update={"stages": [*omissions, *commissions]})
    else:
        spec = PipelineSpec(
            name="no-models",
            stages=[StageSpec(step="upper", implementation="upper.basic")],
        )
        registry = make_registry()

    with pytest.raises(SpecError) as error:
        asyncio.run(evaluate(SOURCE, SUMMARY, spec=spec, registry=registry))

    message = str(error.value)
    assert spec.name in message, message
    assert expected in message, message


def test_the_unchanged_preset_is_recognized_as_validated():
    result = run()

    assert result.validated_preset == "omission_all_opus"
    assert result.warnings == []


def test_the_preset_offers_only_the_strict_level():
    alarm = next(stage for stage in PRESET.stages if stage.step == "alarm")

    assert alarm.settings["thresholds"] == {"strict": 0.90}
    with pytest.raises(SpecError, match="level 'moderate' is not one of"):
        run(with_settings(PRESET, "alarm", level="moderate"))


def test_changing_a_setting_is_not_the_validated_preset_and_warns_about_thresholds():
    result = run(with_settings(PRESET, "check", effort="high"))

    assert result.validated_preset is None
    assert result.warnings == [
        "the alarm thresholds were calibrated for omission_all_opus, "
        "and this pipeline differs from it, so they haven't been calibrated for it"
    ]


def test_new_thresholds_on_a_changed_pipeline_do_not_warn():
    spec = with_settings(PRESET, "check", effort="high")
    spec = with_settings(spec, "alarm", thresholds={"strict": 0.8}, level="strict")

    assert run(spec).warnings == []


def test_an_edited_profile_text_is_not_the_validated_preset():
    clinical = load_builtin_profile("clinical")
    texts = {**clinical.texts, "severity_v1": clinical.texts["severity_v1"] + "\n"}
    edited = clinical.model_copy(update={"texts": texts})

    assert run(profile=edited).validated_preset is None


def test_an_identical_profile_under_another_name_is_still_validated():
    renamed = load_builtin_profile("clinical").model_copy(update={"name": "my-copy"})

    assert run(profile=renamed).validated_preset == "omission_all_opus"


def test_an_invalid_spec_fails_before_any_call():
    bad = with_settings(PRESET, "alarm", level="extreme")
    client = FakeClient(script())

    with pytest.raises(SpecError):
        asyncio.run(evaluate(SOURCE, SUMMARY, spec=bad, client=client))
    assert client.requests == []


def test_without_a_client_or_api_key_it_fails_before_any_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    with pytest.raises(MissingAPIKeyError):
        asyncio.run(evaluate(SOURCE, SUMMARY))


def test_unreadable_settings_are_a_setup_error_without_their_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_bytes(b"ANTHROPIC_API_KEY=\xffsk-ant-secret")

    with pytest.raises(UnreadableSettingsError) as error:
        asyncio.run(evaluate(SOURCE, SUMMARY, env_file=".env"))

    message = str(error.value)
    assert message.startswith("can't read the settings (UnicodeDecodeError).")
    assert str(tmp_path / ".env") in message
    assert "secret" not in message


def test_the_missing_key_message_names_the_stages_and_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(MissingAPIKeyError) as error:
        asyncio.run(evaluate(SOURCE, SUMMARY))

    assert "Needed by: extract, rank, check, refute (claude-opus-5-5)" in str(
        error.value
    )


def test_a_pipeline_that_calls_no_model_needs_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # evaluate() runs only full checks, and the built-in ones all call a model,
    # so the rule is checked on the runner underneath it.
    from groundline_core.evaluate import run_any_pipeline

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", "invalid-unused-policy")
    spec = PipelineSpec(
        name="no-models",
        stages=[StageSpec(step="upper", implementation="upper.basic")],
    )

    result = asyncio.run(
        run_any_pipeline(SOURCE, SUMMARY, spec=spec, registry=make_registry())
    )

    assert result.run.stages["upper"].status == "ran"


def test_injected_client_ignores_invalid_model_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", "invalid-unused-policy")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = run()

    assert result.run.usage.calls == 4


def test_injected_client_rejects_invalid_check_timeout_before_model_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "invalid-private-value")
    client = FakeClient(script())

    with pytest.raises(UnreadableSettingsError) as error:
        asyncio.run(evaluate(SOURCE, SUMMARY, client=client))

    assert "invalid-private-value" not in str(error.value)
    assert client.requests == []


def test_inherited_deadline_does_not_reload_environment_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    # The enclosing caller already established a valid deadline. evaluate()
    # must use it rather than reading changed or unrelated environment values.
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "invalid-unused-timeout")
    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", "invalid-unused-policy")

    async def run_with_deadline():
        with check_deadline(120):
            return await evaluate(SOURCE, SUMMARY, client=FakeClient(script()))

    assert asyncio.run(run_with_deadline()).run.usage.calls == 4


def test_evaluate_sync_blocks_until_done():
    result = evaluate_sync(SOURCE, SUMMARY, client=FakeClient(script()))

    assert result.run.output("alarm", AlarmOutput).flagged is False


# --- spec helpers -------------------------------------------------------------


def test_with_settings_changes_one_stage_and_keeps_the_rest():
    changed = with_settings(PRESET, "check", effort="high")

    assert changed.stages[2].settings["effort"] == "high"
    assert changed.stages[2].settings["prompt"] == "check_batch_v1"
    assert PRESET.stages[2].settings["effort"] == "medium"  # original unchanged


def test_with_settings_needs_a_step_in_the_pipeline():
    with pytest.raises(ValueError, match="no 'polish' stage"):
        with_settings(PRESET, "polish", level="x")


def test_loads_a_spec_file(tmp_path: Path):
    path = tmp_path / "mine.yaml"
    path.write_text(
        "name: mine\nprofile: clinical\nstages:\n"
        "  - {step: extract, implementation: extract.llm}\n"
    )

    spec = load_spec(path)

    assert (spec.name, spec.stages[0].implementation) == ("mine", "extract.llm")


def test_a_malformed_spec_file_is_an_error(tmp_path: Path):
    path = tmp_path / "mine.yaml"
    path.write_text("name: mine\nstages: [{step: extract}]\n")

    with pytest.raises(ValueError, match="mine.yaml"):
        load_spec(path)
