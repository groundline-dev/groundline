"""The errors a library caller can catch: one base, `GroundlineError`, with setup
errors, invalid input and model failures under it. Every test goes through the
public package, as a caller would, and no test calls the real Anthropic API."""

import asyncio
import os
import warnings
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

import groundline_core as gl
from groundline_core import evaluate, evaluate_sync, load_preset
from groundline_core._core import (
    Effort,
    FileCache,
    ModelReply,
    load_profile,
    load_spec,
)
from groundline_core._core.errors import file_line
from tests.anthropic_helpers import api_error, fake_anthropic, message
from tests.async_helpers import finish_before_watchdog
from tests.fakes import FakeClient, HungClient
from tests.omission_helpers import SOURCE, SUMMARY, not_flagged_script

SENTINEL_KEY = "sk-ant-sentinel-0123456789"

PUBLIC_BASES = {
    "GroundlineError": ("Exception",),
    "SetupError": ("GroundlineError",),
    "SpecError": ("SetupError", "ValueError"),
    "ProfileError": ("SetupError", "ValueError"),
    "MissingAPIKeyError": ("SetupError",),
    "APIKeyRejectedError": ("SetupError",),
    "SettingsError": ("SetupError",),
    "InvalidInputError": ("GroundlineError", "ValueError"),
    "ModelFailureError": ("GroundlineError",),
    "ModelTimeoutError": ("ModelFailureError",),
}
"""Each public error's direct base classes. `ModelTimeoutError` is deliberately
not a `TimeoutError`, which the runner reads as its own deadline expiring."""


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's key, settings and .env out of these tests, and make a
    model call fail at its first attempt instead of waiting to retry."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", "1")
    monkeypatch.chdir(tmp_path)


def assert_safe(error: BaseException, *paths: Path) -> None:
    """The message holds no source or summary text, no key and none of `paths`'
    folders."""
    text = str(error)
    for secret in (SOURCE, SUMMARY, SENTINEL_KEY, *(str(p.parent) for p in paths)):
        assert secret not in text, f"{secret[:40]!r} is in the message: {text!r}"


def anthropic_reply(response: httpx2.Response) -> Callable[[str], httpx2.Response]:
    return lambda _prompt: response


# --- the tree ----------------------------------------------------------------


def test_the_public_errors_form_one_tree_under_groundline_error():
    actual = {
        name: tuple(base.__name__ for base in getattr(gl, name).__bases__)
        for name in PUBLIC_BASES
    }

    assert actual == PUBLIC_BASES


# --- setup errors --------------------------------------------------------------


def test_a_spec_file_that_cannot_be_read_names_the_file_only_in_path(tmp_path: Path):
    path = tmp_path / "specs" / "mine.yaml"
    path.parent.mkdir()
    path.write_text("name: [unclosed\n")

    with pytest.raises(gl.SpecError) as error:
        load_spec(path)

    assert error.value.path == path.absolute()
    assert "mine.yaml" in str(error.value)
    assert_safe(error.value, path)


def test_a_profile_that_cannot_be_loaded_is_a_setup_error_with_its_path(
    tmp_path: Path,
):
    folder = tmp_path / "profiles" / "mine"
    folder.mkdir(parents=True)

    with pytest.raises(gl.SetupError) as error:
        load_profile(folder)

    assert error.value.path == (folder / "profile.yaml").absolute()
    assert_safe(error.value, folder / "profile.yaml")


@pytest.mark.parametrize("kind", ["spec", "profile"])
@pytest.mark.parametrize(
    "text",
    [
        f"name: mine\nstages: []\nnot_a_field: {SENTINEL_KEY}\n",
        f"name: [mine\n{SENTINEL_KEY}: 1\n",
        f"name: *{SENTINEL_KEY}\n",
        f"name: mine\nstages: []\n{SENTINEL_KEY}: 1\n",
        f"name: mine\nstages:\n- step: extract\n  {SENTINEL_KEY}: 1\n",
    ],
    ids=[
        "unexpected field",
        "invalid YAML",
        "undefined alias",
        "key as a field name",
        "key as a field name in a stage",
    ],
)
def test_a_spec_or_profile_error_never_repeats_what_the_file_holds(
    kind: str, text: str, tmp_path: Path
):
    folder = tmp_path / "mine"
    folder.mkdir()
    path = folder / "profile.yaml"
    path.write_text(text)
    load = load_spec if kind == "spec" else load_profile

    with pytest.raises(gl.SetupError) as error:
        load(path if kind == "spec" else folder)

    assert_safe(error.value, path)


def test_a_key_pasted_as_a_name_in_a_spec_is_redacted():
    preset = load_preset("omission_all_opus")
    stages = [
        stage.model_copy(update={"implementation": SENTINEL_KEY})
        if stage.step == "rank"
        else stage
        for stage in preset.stages
    ]
    spec = preset.model_copy(update={"stages": stages})

    with pytest.raises(gl.SpecError) as error:
        evaluate_sync(SOURCE, SUMMARY, spec=spec, client=FakeClient([]))

    assert "unknown implementation '<redacted key>'" in str(error.value)
    assert all(SENTINEL_KEY not in problem for problem in error.value.problems)
    assert_safe(error.value)


@pytest.mark.parametrize(
    "name", ["extract_coarse_v1", "gl_short", "sk-ant", "my-stage_2"]
)
def test_names_that_are_not_key_shaped_are_kept(name: str):
    assert str(gl.SetupError(f"unknown step '{name}'")) == f"unknown step '{name}'"


def test_a_profile_text_file_that_is_missing_is_named_without_its_folders(
    tmp_path: Path,
):
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "profile.yaml").write_text(
        "name: mine\nplaceholders: {source: a, summary: b}\n"
        "texts: {a: ../private-folder-name/x.txt}\n"
    )

    with pytest.raises(gl.ProfileError) as error:
        load_profile(folder)

    assert "text 'a' file x.txt not found" in str(error.value)
    assert "private-folder-name" not in str(error.value)


def test_a_spec_error_names_the_field_at_fault(tmp_path: Path):
    path = tmp_path / "mine.yaml"
    path.write_text(f"name: mine\nstages: []\nnot_a_field: {SENTINEL_KEY}\n")

    with pytest.raises(gl.SpecError) as error:
        load_spec(path)

    assert "mine.yaml: not_a_field: Extra inputs are not permitted" in (
        error.value.problems
    )


@pytest.mark.parametrize("kind", ["spec", "profile"])
def test_a_spec_or_profile_that_is_not_utf8_is_a_setup_error(kind: str, tmp_path: Path):
    folder = tmp_path / "mine"
    folder.mkdir()
    path = folder / "profile.yaml"
    path.write_bytes(b"name: caf\xe9\n")
    load = load_spec if kind == "spec" else load_profile

    with pytest.raises(gl.SetupError) as error:
        load(path if kind == "spec" else folder)

    assert error.value.path == path.absolute()
    assert "profile.yaml" in str(error.value)
    assert_safe(error.value, path)


def test_a_model_the_api_does_not_have_is_a_setup_error_not_a_model_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    sdk_text = f"model: no-such-model (while reading {SOURCE})"
    missing = httpx2.Response(404, json=api_error("not_found_error", sdk_text))
    sent = fake_anthropic(monkeypatch, anthropic_reply(missing))

    with pytest.raises(gl.SetupError) as error:
        evaluate_sync(SOURCE, SUMMARY, anthropic_api_key=SENTINEL_KEY)

    assert not isinstance(error.value, gl.ModelFailureError)
    assert str(error.value).startswith("the Anthropic API has no model ")
    assert "while reading" not in str(error.value)
    assert_safe(error.value)
    assert len(sent) == 1  # not retried


def test_a_missing_key_names_the_env_file_only_in_path(tmp_path: Path):
    env_file = tmp_path / "config" / "app.env"

    with pytest.raises(gl.MissingAPIKeyError) as error:
        evaluate_sync(SOURCE, SUMMARY, env_file=env_file)

    assert error.value.path == env_file.absolute()
    assert "Looked in: the environment, and app.env" in str(error.value)
    assert "not found" not in str(error.value)
    assert_safe(error.value, env_file)


def test_unreadable_settings_are_a_settings_error_without_the_folder(tmp_path: Path):
    env_file = tmp_path / "config" / "app.env"
    env_file.parent.mkdir()
    env_file.write_bytes(b"ANTHROPIC_API_KEY=\xff" + SENTINEL_KEY.encode())

    with pytest.raises(gl.SettingsError) as error:
        evaluate_sync(SOURCE, SUMMARY, env_file=env_file)

    assert error.value.path == env_file.absolute()
    assert_safe(error.value, env_file)


def test_a_rejected_key_is_a_setup_error_naming_neither_key_nor_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    env_file = tmp_path / "config" / "app.env"
    env_file.parent.mkdir()
    env_file.write_text(f"ANTHROPIC_API_KEY={SENTINEL_KEY}\n")
    rejected = httpx2.Response(
        401, json=api_error("authentication_error", "invalid x-api-key")
    )
    fake_anthropic(monkeypatch, anthropic_reply(rejected))

    with pytest.raises(gl.APIKeyRejectedError) as error:
        evaluate_sync(SOURCE, SUMMARY, env_file=env_file)

    assert error.value.path == env_file.absolute()
    assert "Key from" not in str(error.value)
    assert_safe(error.value, env_file)


def test_a_client_and_a_key_together_are_a_setup_error_before_any_call():
    client = FakeClient(not_flagged_script())

    with pytest.raises(gl.SetupError, match="client or anthropic_api_key, not both"):
        evaluate_sync(SOURCE, SUMMARY, client=client, anthropic_api_key=SENTINEL_KEY)

    assert client.requests == []


# --- invalid input -----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", " \n\t"), ("summary", ""), ("source", 42)],
    ids=["blank source", "empty summary", "source not text"],
)
def test_a_source_or_summary_that_cannot_be_checked_is_invalid_input(
    field: str, value: object
):
    client = FakeClient(not_flagged_script())
    texts: dict[str, object] = {"source": SOURCE, "summary": SUMMARY, field: value}

    with pytest.raises(gl.InvalidInputError, match=field) as error:
        evaluate_sync(texts["source"], texts["summary"], client=client)  # pyright: ignore[reportArgumentType]

    assert client.requests == []
    assert_safe(error.value)


def test_invalid_input_is_reported_before_a_bad_spec():
    preset = load_preset("omission_all_opus")
    spec = preset.model_copy(update={"stages": preset.stages[:1]})  # extract only

    with pytest.raises(gl.InvalidInputError):
        evaluate_sync(SOURCE, "", spec=spec, client=FakeClient([]))


# --- model failures ----------------------------------------------------------


def test_an_anthropic_error_is_a_model_failure_that_names_it_without_quoting_it(
    monkeypatch: pytest.MonkeyPatch,
):
    sdk_text = f"Overloaded while reading {SOURCE}"
    overloaded = httpx2.Response(529, json=api_error("overloaded_error", sdk_text))
    fake_anthropic(monkeypatch, anthropic_reply(overloaded))

    with pytest.raises(gl.ModelFailureError) as error:
        evaluate_sync(SOURCE, SUMMARY, anthropic_api_key=SENTINEL_KEY)

    assert error.value.step == "extract"
    assert str(error.value) == (
        "stage 'extract' failed: the Anthropic API answered 529 (OverloadedError)"
    )
    assert "Overloaded while reading" not in str(error.value)
    assert_safe(error.value)


def test_a_refusal_is_a_model_failure(monkeypatch: pytest.MonkeyPatch):
    refusal = httpx2.Response(200, json=message(stop_reason="refusal"))
    fake_anthropic(monkeypatch, anthropic_reply(refusal))

    with pytest.raises(gl.ModelFailureError, match="declined to answer") as error:
        evaluate_sync(SOURCE, SUMMARY, anthropic_api_key=SENTINEL_KEY)

    assert error.value.step == "extract"


def test_a_reply_that_stays_unusable_is_a_model_failure_without_its_text():
    client = FakeClient([f"Not JSON. {SOURCE}", f"Still not JSON. {SUMMARY}"])

    with pytest.raises(gl.ModelFailureError) as error:
        evaluate_sync(SOURCE, SUMMARY, client=client)

    assert error.value.step == "extract"
    assert "remained unusable after a request for correction" in str(error.value)
    assert_safe(error.value)


def test_a_model_that_never_answers_ends_in_a_model_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "0.01")
    check = evaluate(SOURCE, SUMMARY, client=HungClient())

    with pytest.raises(gl.ModelTimeoutError):
        asyncio.run(finish_before_watchdog(check))


class CallersOwnError(Exception):
    pass


class FailingClient:
    """A caller's own model client, raising whatever it was given."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        raise self.error


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any folder")
def test_a_cache_that_cannot_be_written_warns_and_the_check_still_finishes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    cache = FileCache(locked / "cache")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = evaluate_sync(
                SOURCE, SUMMARY, client=FakeClient(not_flagged_script()), cache=cache
            )
    finally:
        locked.chmod(0o700)

    assert result.flagged is False
    assert "couldn't write to the stage cache" in caplog.text


def test_the_callers_own_exception_comes_out_unwrapped():
    raised = CallersOwnError("my connection pool is closed")

    with pytest.raises(CallersOwnError) as error:
        evaluate_sync(SOURCE, SUMMARY, client=FailingClient(raised))

    assert error.value is raised
    assert not isinstance(error.value, gl.GroundlineError)


def test_a_callers_client_can_report_a_model_failure():
    client = FailingClient(gl.ModelFailureError("my provider is down"))

    with pytest.raises(gl.ModelFailureError) as error:
        evaluate_sync(SOURCE, SUMMARY, client=client)

    assert error.value.step == "extract"
    assert str(error.value) == "stage 'extract' failed: my provider is down"


def test_a_file_line_for_a_folder_that_cannot_be_searched_still_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def unsearchable(self: Path) -> bool:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "exists", unsearchable)

    assert file_line(tmp_path / ".env") == f"\n  File: {tmp_path / '.env'}"
