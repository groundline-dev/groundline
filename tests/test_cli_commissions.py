import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from groundline import cli
from tests.commissions_helpers import (
    DOSE,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    ISSUES,
    SUMMARY,
    Script,
    issue,
    issues_reply,
    support_reply,
)
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE

runner = CliRunner()


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROUNDLINE_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "transcript.txt").write_text(SOURCE)
    (tmp_path / "note.txt").write_text(SUMMARY)
    return tmp_path / "transcript.txt", tmp_path / "note.txt"


def use_client(
    monkeypatch: pytest.MonkeyPatch, script: Callable[[str], str] | None
) -> None:
    monkeypatch.setattr(
        cli, "make_client", lambda: FakeClient(script) if script else None
    )


def commissions(files: tuple[Path, Path], *args: str) -> Result:
    return runner.invoke(cli.app, ["commissions", str(files[0]), str(files[1]), *args])


def test_reports_a_flagged_summary_and_exits_with_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert result.exit_code == 1, result.output
    out = result.stdout
    assert "Commission check  transcript.txt · note.txt" in out
    assert "commission_quick · clinical profile (validated preset)" in out
    assert "FLAGGED (alarm 0.95 ≥ 0.85, default)" in out
    assert "severity    verdict       confidence  claim" in out
    assert f"critical    contradicted  0.95        {DOSE}" in out
    assert f'source: "{DOSE_SOURCE}"' in out
    assert f"supporting  unsupported   0.70        {EXAM}" in out
    assert "1 calls" in out


def test_commissions_are_listed_by_severity_then_confidence(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    # The check lists the supporting commission first; the report still leads with the
    # critical one.
    use_client(
        monkeypatch, Script(commissions_check=issues_reply(EXAM_ISSUE, DOSE_ISSUE))
    )

    result = commissions(files)

    assert result.stdout.index(DOSE) < result.stdout.index(EXAM)


def test_a_quote_not_found_in_the_source_is_shown_and_marked(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    made_up = issue(DOSE, "contradicted", 95, "critical", "the doctor said 400 mg")
    use_client(monkeypatch, Script(commissions_check=issues_reply(made_up)))

    result = commissions(files)

    assert (
        'source: "the doctor said 400 mg" (not found word for word in the source)'
        in result.stdout
    )


def test_a_contradicted_claim_with_no_quoted_passage_says_so(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    bare = issue(DOSE, "contradicted", 95, "critical", None)
    use_client(monkeypatch, Script(commissions_check=issues_reply(bare)))

    result = commissions(files)

    assert "source: no passage quoted" in result.stdout


def test_a_verified_quote_has_no_marker(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert "not found word for word" not in result.stdout
    assert "no passage quoted" not in result.stdout


def test_only_a_contradicted_claim_shows_a_source_line(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert result.stdout.count("source:") == 1


def test_a_summary_that_is_not_flagged_exits_with_0(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=issues_reply(EXAM_ISSUE)))

    result = commissions(files)

    assert result.exit_code == 0, result.output
    assert "NOT FLAGGED (alarm 0.00 < 0.85, default)" in result.stdout
    assert f"supporting  unsupported   0.70        {EXAM}" in result.stdout


def test_a_summary_with_no_issues_says_none(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=issues_reply()))

    result = commissions(files)

    assert result.exit_code == 0
    assert "Not supported by the source (severity order):\n  none" in result.stdout


def test_the_cost_is_priced_for_haiku(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert "no price for" not in result.stdout
    # A Haiku call this small costs a hundredth of a cent, which must not read as free.
    assert "Cost              $0.0001 · 1 calls" in result.stdout


def test_the_default_level_can_be_named_with_threshold(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files, "--threshold", "default")

    assert "(alarm 0.95 ≥ 0.85, default)" in result.stdout
    assert "(validated preset)" in result.stdout


def test_an_unknown_threshold_exits_with_2_before_any_call(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    client = FakeClient([])
    monkeypatch.setattr(cli, "make_client", lambda: client)

    result = commissions(files, "--threshold", "extreme")

    assert result.exit_code == 2
    assert "level 'extreme' is not one of the thresholds" in result.stderr
    assert client.requests == []


def test_a_missing_key_exits_with_2_and_a_clear_message(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, None)  # use the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = commissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ANTHROPIC_API_KEY is not set.")
    assert "Needed by: commissions_check (claude-haiku-5-5)" in result.stderr
    assert "copy .env.example to .env" in result.stderr
    assert "Traceback" not in result.output


def test_an_unusable_reply_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check="not json"))

    result = commissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: stage 'commissions_check' failed")
    assert "Cost before stopping: $0.0001 · 2 calls · " in result.stderr


def test_a_missing_input_file_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = runner.invoke(cli.app, ["commissions", "nope.txt", str(files[1])])

    assert result.exit_code == 2


def test_json_writes_the_full_result(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    out_path = files[0].parent / "result.json"

    commissions(files, "--json", str(out_path))

    data = json.loads(out_path.read_text())
    assert data["flagged"] is True
    assert data["validated_preset"] == "commission_quick"
    assert data["outputs"]["alarm"]["score"] == 0.95
    assert [
        i["quote"] for i in data["outputs"]["commissions_check"]["commissions"]
    ] == [
        DOSE,
        EXAM,
    ]
    assert [c["step"] for c in data["calls"]] == ["commissions_check"]
    assert data["stages"]["commissions_refute"]["status"] == "disabled"
    assert data["usage"]["calls"] == 1


def test_cache_warns_and_makes_reruns_free(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    first = commissions(files, "--cache")
    second = commissions(files, "--cache")

    assert "caching is on" in first.stderr
    assert "don't use it with sensitive data" in first.stderr
    assert "1 calls (1 cached)" in second.stdout
    assert "$0.00" in second.stdout


def test_without_cache_nothing_is_written(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert "caching" not in result.stderr
    assert not (files[0].parent / "cache").exists()


def preset_text() -> str:
    return (Path(cli.__file__).parent / "presets" / "commission_quick.yaml").read_text()


def test_a_custom_pipeline_is_not_the_validated_preset(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    custom = files[0].parent / "mine.yaml"
    custom.write_text(
        preset_text()
        .replace("name: commission_quick", "name: mine")
        .replace(
            "effort: medium, max_tokens: 16000, prompt: quick_check_v1",
            "effort: high, max_tokens: 16000, prompt: quick_check_v1",
        )
    )

    result = commissions(files, "--pipeline", str(custom))

    assert "mine · clinical profile (not a validated preset)" in result.stdout
    assert "Warning           the alarm thresholds were calibrated" in result.stdout


def test_refute_can_be_switched_on_in_a_custom_pipeline(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    script = Script(
        commissions_check=ISSUES,
        commissions_refute=support_reply(
            ("q1", True, "take motrin 600 mg every 6 hours")
        ),
    )
    use_client(monkeypatch, script)
    custom = files[0].parent / "refuting.yaml"
    custom.write_text(
        preset_text()
        .replace("name: commission_quick", "name: refuting")
        .replace("    enabled: false\n", "")
    )

    result = commissions(files, "--pipeline", str(custom))

    assert result.exit_code == 0, result.output
    assert "NOT FLAGGED" in result.stdout
    assert "Cleared by refute" in result.stdout
    assert f"supporting  unsupported   0.70        {EXAM}" in result.stdout
    assert DOSE not in result.stdout


def test_the_report_says_nothing_about_refute_when_it_is_off(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))

    result = commissions(files)

    assert "Cleared by refute" not in result.stdout


def test_a_pipeline_without_the_report_steps_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    custom = files[0].parent / "short.yaml"
    custom.write_text(
        "name: short\nprofile: clinical\nstages:\n"
        "  - {step: commissions_check, implementation: commissions_check.llm_quick}\n"
    )

    result = commissions(files, "--pipeline", str(custom))

    assert result.exit_code == 2
    assert "needs 'commissions_refute' and 'alarm' stages" in result.stderr
    assert "for the commissions report" in result.stderr


def test_a_pipeline_that_is_not_a_commission_check_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    omission = Path(cli.__file__).parent / "presets" / "omission_all_opus.yaml"

    result = commissions(files, "--pipeline", str(omission))

    assert result.exit_code == 2
    assert "needs 'commissions_refute' and 'alarm' stages" in result.stderr


def test_help_lists_the_commissions_command():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "commissions" in result.stdout
    assert "omissions" in result.stdout


def test_an_unwritable_json_path_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(commissions_check=ISSUES))
    out_path = files[0].parent / "no-such-folder" / "result.json"

    result = commissions(files, "--json", str(out_path))

    assert result.exit_code == 2
    assert result.stderr.startswith("error: couldn't write the JSON result")
    assert "Traceback" not in result.output


def test_an_unexpected_error_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    def broken(*args: object, **kwargs: object) -> None:
        raise KeyError("a bug")

    monkeypatch.setattr(cli, "evaluate_sync", broken)

    result = commissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: unexpected KeyError: 'a bug'")
    assert "Traceback" not in result.output
