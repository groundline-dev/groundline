import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from groundline_core import GroundlineError, cli
from groundline_core._disclaimer import SHORT, full_disclaimer
from groundline_core.profiles import load_builtin_profile
from tests.anthropic_helpers import fake_anthropic, keys_sent
from tests.fakes import FakeClient, unwritable_folder
from tests.omission_helpers import (
    SOURCE,
    SUMMARY,
    Script,
    flagged_script,
    not_flagged_script,
)

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


def omissions(files: tuple[Path, Path], *args: str) -> Result:
    return runner.invoke(cli.app, ["omissions", str(files[0]), str(files[1]), *args])


def test_reports_a_summary_that_is_not_flagged(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())

    result = omissions(files)

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Omission check   transcript.txt · note.txt" in out
    assert "omission_all_opus · clinical profile (validated preset)" in out
    assert "NOT FLAGGED (alarm 0.00 < 0.90, strict)" in out
    assert "Omission score   1 (higher is better; below 0.5 is flagged)" in out
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
    assert "Omission score   0.25 (" in result.stdout
    assert "critical    absent   0.95       Motrin 600 mg" in result.stdout


def test_a_score_on_the_threshold_is_not_rounded_up_to_one_half(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    script = flagged_script()
    checked = json.loads(script.replies["check"][0])
    checked["facts"][0]["missing_confidence"] = 90  # exactly the strict threshold
    script.replies["check"] = [json.dumps(checked)]
    use_client(monkeypatch, script)

    result = omissions(files)

    assert result.exit_code == 1
    assert "Omission score   0.4999 (" in result.stdout


def test_threshold_is_no_longer_an_option(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    client = FakeClient([])
    monkeypatch.setattr(cli, "make_client", lambda: client)

    result = omissions(files, "--threshold", "strict")

    assert result.exit_code == 2
    assert "No such option" in result.stderr
    assert client.requests == []


def test_the_key_comes_from_the_dotenv_in_the_current_folder(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, None)  # the real Anthropic client, on a fake transport
    sent = fake_anthropic(monkeypatch, not_flagged_script())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    result = omissions(files)

    assert result.exit_code == 0, result.output
    assert keys_sent(sent) == {"sk-dotenv"}


def test_a_missing_key_exits_with_2_and_a_clear_message(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, None)  # use the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ANTHROPIC_API_KEY is not set.")
    assert f"\n  File: {Path.cwd() / '.env'} (not found)\n" in result.stderr
    assert "Traceback" not in result.output


def test_a_setup_error_names_a_file_that_exists_without_not_found(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, None)  # use the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    Path(".env").write_text("OTHER_SETTING=x\n")

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.endswith(f"\n  File: {Path.cwd() / '.env'}\n")


def test_an_unusable_reply_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, Script(extract="not json"))

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: stage 'extract' failed")
    assert re.search(
        r"Recorded cost before stopping: \$0\.\d{4} · 2 calls · ", result.stderr
    )


def test_a_summary_that_is_not_utf8_is_invalid_input_not_a_bug(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    files[1].write_bytes(b"Knee pain \xff")

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr == f"error: {files[1].name} isn't readable UTF-8 text\n"


def test_a_value_error_from_inside_groundline_is_reported_as_a_bug(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    def broken(*args: object, **kwargs: object) -> None:
        raise ValueError("no verdict for f3")

    monkeypatch.setattr(cli, "evaluate_sync", broken)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: unexpected ValueError\n")
    assert "This is a bug in groundline; please report it." in result.stderr


def test_a_missing_input_file_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())

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
    assert data["outputs"]["alarm"]["rescaled_score"] == pytest.approx(0.25)
    assert [c["step"] for c in data["calls"]] == ["extract", "rank", "check", "refute"]
    assert data["usage"]["calls"] == 4
    clinical = load_builtin_profile("clinical").disclaimer
    assert data.get("disclaimer") == full_disclaimer(clinical), sorted(data)


def test_cache_warns_and_makes_reruns_free(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    first = omissions(files, "--cache")
    second = omissions(files, "--cache")

    assert "caching is on" in first.stderr
    assert "don't use it with sensitive data" in first.stderr
    assert "4 calls (4 cached)" in second.stdout
    assert "$0.00" in second.stdout


def test_without_cache_nothing_is_written(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())

    result = omissions(files)

    assert "caching" not in result.stderr
    assert not (files[0].parent / "cache").exists()


def test_a_custom_pipeline_is_not_the_validated_preset(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
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


def test_a_pipeline_that_is_not_a_full_check_exits_with_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    custom = files[0].parent / "short.yaml"
    custom.write_text(
        "name: short\nprofile: clinical\nstages:\n"
        "  - {step: extract, implementation: extract.llm}\n"
    )

    result = omissions(files, "--pipeline", str(custom))

    assert result.exit_code == 2
    assert "doesn't make a full check" in result.stderr
    assert "score and rank (omissions)" in result.stderr


def test_the_report_ends_with_the_short_disclaimer(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())

    result = omissions(files)

    assert result.exit_code == 0, result.output
    assert result.stdout.endswith(f"\n\n{SHORT}\n"), result.stdout[-300:]


@pytest.mark.parametrize("args", [[], ["omissions"], ["commissions"]])
def test_help_ends_with_the_short_disclaimer(args: list[str]):
    result = runner.invoke(cli.app, [*args, "--help"])

    assert result.exit_code == 0, result.output
    # The help is wrapped to the terminal's width.
    assert SHORT in " ".join(result.stdout.split()), result.stdout


def test_help_lists_the_omissions_command():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "omissions" in result.stdout


def test_an_unwritable_json_path_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    out_path = files[0].parent / "no-such-folder" / "result.json"

    result = omissions(files, "--json", str(out_path))

    assert result.exit_code == 2
    assert result.stderr == (
        "error: couldn't write the JSON result to result.json: "
        f"No such file or directory\n  File: {out_path}\n"
    )
    assert "Traceback" not in result.output


def test_an_unexpected_error_exits_with_2_not_1(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    def broken(*args: object, **kwargs: object) -> None:
        raise KeyError("a bug")

    monkeypatch.setattr(cli, "evaluate_sync", broken)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: unexpected KeyError\n")
    assert "Traceback" not in result.output


def test_cli_reports_check_deadline_with_exit_code_2(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from typing import Any

    from groundline_core._core import ModelReply

    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "0.5")
    cancelled: list[bool] = []

    class HungClient:
        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            try:
                async with asyncio.timeout(3):
                    await asyncio.Event().wait()
            except TimeoutError:
                raise AssertionError("test watchdog fired") from None
            finally:
                cancelled.append(True)
            raise AssertionError("hung client returned")

    monkeypatch.setattr(cli, "make_client", lambda: HungClient())
    result = omissions(files)
    assert result.exit_code == 2
    assert cancelled == [True]
    assert "unexpected" not in result.output.lower()
    assert "test watchdog" not in result.output
    assert "AssertionError" not in result.output
    assert any(
        word in result.output.lower()
        for word in ("timed out", "time limit", "deadline", "timeout")
    )


def test_the_clis_own_errors_are_not_groundline_errors():
    # A library caller who catches GroundlineError never sees these, so they
    # stay out of the three kinds the library documents.
    for error in (cli.UsageError, cli.JSONWriteError):
        assert not issubclass(error, GroundlineError)


def test_a_cache_that_cannot_be_written_warns_in_the_clis_own_style(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    cache_dir = unwritable_folder(files[0].parent)
    monkeypatch.setenv("GROUNDLINE_CACHE_DIR", str(cache_dir))

    result = omissions(files, "--cache")

    assert result.exit_code == 0, result.output
    assert "\nwarning: couldn't write to the stage cache (" in result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert "runner.py" not in result.stderr


def test_an_unexpected_error_is_reported_by_type_only(
    files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    # A bug's message is text groundline didn't write: a key from a caller's
    # client, or a pydantic error quoting a model reply that repeats the source.
    def leaky(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"refused sk-ant-sentinel-0123456789 for {SOURCE}")

    monkeypatch.setattr(cli, "evaluate_sync", leaky)

    result = omissions(files)

    assert result.exit_code == 2
    assert result.stderr == (
        "error: unexpected RuntimeError\n"
        "  This is a bug in groundline; please report it.\n"
    )


@pytest.mark.parametrize("option", ["--json", "--pipeline"])
def test_a_key_shaped_file_name_is_redacted_on_both_lines(
    option: str, files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    use_client(monkeypatch, not_flagged_script())
    key_named = files[0].parent / "sk-ant-sentinel-0123456789.yaml"
    if option == "--pipeline":
        key_named.write_text("name: [broken\n")
    else:
        key_named = files[0].parent / "no-such-folder" / key_named.name

    result = omissions(files, option, str(key_named))

    assert result.exit_code == 2
    assert "sk-ant-sentinel" not in result.stderr
    assert "\n  File: " in result.stderr
    assert result.stderr.count("<redacted key>") == 2
