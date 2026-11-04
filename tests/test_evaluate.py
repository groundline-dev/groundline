import asyncio
from pathlib import Path

import pytest

from groundline import (
    EvaluationResult,
    builtin_presets,
    evaluate,
    evaluate_sync,
    load_preset,
)
from groundline.core import (
    DomainProfile,
    PipelineSpec,
    SpecError,
    StageSpec,
    load_spec,
    with_settings,
)
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.omissions.models import ScoreOutput
from groundline.profiles import load_builtin_profile
from groundline.settings import MissingAPIKeyError, UnreadableSettingsError
from tests.core_fakes import make_registry
from tests.fakes import FakeClient
from tests.omission_helpers import (
    EXTRACTED,
    RANKED,
    SOURCE,
    SUMMARY,
    Script,
    check_reply,
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


def test_the_unchanged_preset_is_recognised_as_validated():
    result = run()

    assert result.validated_preset == "omission_all_opus"
    assert result.warnings == []


def test_choosing_the_moderate_level_is_still_the_validated_preset():
    result = run(with_settings(PRESET, "alarm", level="moderate"))

    assert result.validated_preset == "omission_all_opus"
    assert result.run.output("alarm", AlarmOutput).threshold == 0.425


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
        asyncio.run(evaluate(SOURCE, SUMMARY))

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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    spec = PipelineSpec(
        name="no-models",
        stages=[StageSpec(step="upper", implementation="upper.basic")],
    )

    result = asyncio.run(evaluate(SOURCE, SUMMARY, spec=spec, registry=make_registry()))

    assert result.run.stages["upper"].status == "ran"


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
