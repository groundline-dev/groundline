import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from groundline import cli
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

runner = CliRunner()

NOT_FLAGGED = Script(
    extract=EXTRACTED,
    rank=RANKED,
    check=check_reply(),
    refute=refute_reply(("f1", "no", None), ("f2", "no", None)),
)


def flagged_script() -> Script:
    check = json.loads(check_reply({"f3": 95}))
    check["facts"][0].update(verdict="absent", quote=None)
    return Script(
        extract=EXTRACTED,
        rank=RANKED,
        check=json.dumps(check),
        refute=refute_reply(("f3", "no", None), ("f1", "no", None), ("f2", "no", None)),
    )


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


def omissions(files: tuple[Path, Path], *args: str) -> Result:
    return runner.invoke(cli.app, ["omissions", str(files[0]), str(files[1]), *args])


def test_reports_a_summary_that_is_not_flagged(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))

    result = omissions(files)

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Omission check   transcript.txt · note.txt" in out
    assert "omission_all_opus · clinical profile (validated preset)" in out
    assert "NOT FLAGGED (alarm 0.00 < 0.90, strict)" in out
    assert "supporting  partial  0.60       Injured the finger skiing." in out
    assert "supporting  absent   0.95       Pain is worse with movement." in out
    assert "3 facts: 1 present · 1 partial · 1 absent" in out
    assert "4 calls" in out


def test_a_flagged_summary_exits_with_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, flagged_script())

    result = omissions(files)

    assert result.exit_code == 1
    assert "FLAGGED (alarm 0.95 ≥ 0.90, strict)" in result.stdout
    assert "critical    absent   0.95       Motrin 600 mg" in result.stdout


def test_the_moderate_threshold_is_still_the_validated_preset(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))

    result = omissions(files, "--threshold", "moderate")

    assert "(alarm 0.00 < 0.425, moderate)" in result.stdout
    assert "(validated preset)" in result.stdout


def test_an_unknown_threshold_exits_with_2_before_any_call(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    client = FakeClient([])
    monkeypatch.setattr(cli, "make_client", lambda: client)

    result = omissions(files, "--threshold", "extreme")

    assert result.exit_code == 2
    assert "level 'extreme' is not one of the thresholds" in result.stderr
    assert client.requests == []


def test_a_missing_key_exits_with_2_and_a_clear_message(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, None)  # use the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ANTHROPIC_API_KEY is not set.")
    assert "Traceback" not in result.output


def test_an_unusable_reply_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(extract="not json"))

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: stage 'extract' failed")


def test_a_missing_input_file_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))

    result = runner.invoke(cli.app, ["omissions", "nope.txt", str(files[1])])

    assert result.exit_code == 2


def test_json_writes_the_full_result(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, flagged_script())
    out_path = files[0].parent / "result.json"

    omissions(files, "--json", str(out_path))

    data = json.loads(out_path.read_text())
    assert data["flagged"] is True
    assert data["validated_preset"] == "omission_all_opus"
    assert data["outputs"]["alarm"]["score"] == 0.95
    assert [c["step"] for c in data["calls"]] == ["extract", "rank", "check", "refute"]
    assert data["usage"]["calls"] == 4


def test_cache_warns_and_makes_reruns_free(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))
    first = omissions(files, "--cache")
    second = omissions(files, "--cache")

    assert "caching is on" in first.stderr
    assert "don't use it with sensitive data" in first.stderr
    assert "4 calls (4 cached)" in second.stdout
    assert "$0.00" in second.stdout


def test_without_cache_nothing_is_written(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))

    result = omissions(files)

    assert "caching" not in result.stderr
    assert not (files[0].parent / "cache").exists()


def test_a_custom_pipeline_is_not_the_validated_preset(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))
    preset = (
        Path(cli.__file__).parent / "presets" / "omission_all_opus.yaml"
    ).read_text()
    custom = files[0].parent / "mine.yaml"
    custom.write_text(
        preset.replace("name: omission_all_opus", "name: mine").replace(
            "effort: medium", "effort: high"
        )
    )

    result = omissions(files, "--pipeline", str(custom))

    assert "mine · clinical profile (not a validated preset)" in result.stdout
    assert "Warning          the alarm thresholds were calibrated" in result.stdout


def test_a_pipeline_without_score_and_alarm_stages_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))
    custom = files[0].parent / "short.yaml"
    custom.write_text(
        "name: short\nprofile: clinical\nstages:\n"
        "  - {step: extract, implementation: extract.llm}\n"
    )

    result = omissions(files, "--pipeline", str(custom))

    assert result.exit_code == 2
    assert "needs 'score' and 'alarm' stages" in result.stderr


def test_help_lists_the_omissions_command():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "omissions" in result.stdout


def test_an_unwritable_json_path_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(**NOT_FLAGGED.replies))
    out_path = files[0].parent / "no-such-folder" / "result.json"

    result = omissions(files, "--json", str(out_path))

    assert result.exit_code == 2
    assert result.stderr.startswith("error: couldn't write the JSON result")
    assert "Traceback" not in result.output


def test_an_unexpected_error_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    def broken(*args: object, **kwargs: object) -> None:
        raise KeyError("a bug")

    monkeypatch.setattr(cli, "evaluate_sync", broken)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: unexpected KeyError: 'a bug'")
    assert "Traceback" not in result.output
