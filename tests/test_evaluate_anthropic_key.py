"""Where `evaluate` gets the Anthropic API key: an argument, the environment, or
an env file the caller names. The real Anthropic client runs on a fake HTTP
transport, so no test calls the real API."""

import asyncio
import json
import logging
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from groundline_core import evaluate, evaluate_sync
from groundline_core.clients.anthropic import AnthropicClient
from groundline_core.core import APIKeyRejectedError
from groundline_core.evaluators.report import result_json
from groundline_core.settings import MissingAPIKeyError
from tests.anthropic_helpers import api_error, fake_anthropic, keys_sent
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE, SUMMARY, not_flagged_script

SENTINEL = "sk-ant-sentinel-0123456789"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real key and .env out of these tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    return fake_anthropic(monkeypatch, not_flagged_script())


# --- R1: the key as an argument ---------------------------------------------


@pytest.mark.parametrize("argument", ["sk-arg", SecretStr("sk-arg")])
def test_the_argument_is_the_key_sent_to_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    sent: list[httpx2.Request],
    argument: str | SecretStr,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    asyncio.run(evaluate(SOURCE, SUMMARY, anthropic_api_key=argument))

    assert sent
    assert keys_sent(sent) == {"sk-arg"}


def test_evaluate_sync_takes_the_key_too(sent: list[httpx2.Request]):
    evaluate_sync(SOURCE, SUMMARY, anthropic_api_key="sk-arg")

    assert keys_sent(sent) == {"sk-arg"}


def test_a_client_and_a_key_together_are_refused_before_any_call():
    client = FakeClient(not_flagged_script())

    with pytest.raises(ValueError, match="client or anthropic_api_key, not both"):
        evaluate_sync(SOURCE, SUMMARY, client=client, anthropic_api_key="sk-arg")
    assert client.requests == []


# --- R2: no .env unless the caller asks --------------------------------------


def test_a_dotenv_in_the_current_folder_is_ignored_by_default(
    sent: list[httpx2.Request],
):
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    with pytest.raises(MissingAPIKeyError):
        evaluate_sync(SOURCE, SUMMARY)
    assert sent == []


def test_an_env_file_is_read_when_the_caller_names_it(
    sent: list[httpx2.Request], tmp_path: Path
):
    env_file = tmp_path / "app.env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    evaluate_sync(SOURCE, SUMMARY, env_file=env_file)

    assert keys_sent(sent) == {"sk-dotenv"}


# --- R3: the environment still works -----------------------------------------


def test_the_environment_key_is_used_without_an_argument(
    monkeypatch: pytest.MonkeyPatch, sent: list[httpx2.Request]
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    evaluate_sync(SOURCE, SUMMARY)

    assert keys_sent(sent) == {"sk-env"}


# --- R4: a missing key fails first, without a path ---------------------------


def test_a_missing_key_fails_before_any_call_and_names_no_path(
    sent: list[httpx2.Request], tmp_path: Path
):
    with pytest.raises(MissingAPIKeyError) as error:
        evaluate_sync(SOURCE, SUMMARY)

    assert sent == []
    message = str(error.value)
    assert message.startswith("The Anthropic API key is not set.")
    assert "Needed by: extract, rank, check, refute (claude-opus-5-5)" in message
    assert str(tmp_path) not in message
    assert ".env" not in message


# --- R5: the key never shows ------------------------------------------------


def test_the_key_appears_nowhere_in_the_result_or_the_logs(
    sent: list[httpx2.Request], caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)

    result = evaluate_sync(SOURCE, SUMMARY, anthropic_api_key=SENTINEL)

    assert keys_sent(sent) == {SENTINEL}  # it was really used
    assert caplog.records  # the SDK's debug logging really ran
    assert SENTINEL not in repr(result)
    assert SENTINEL not in str(result)
    assert SENTINEL not in json.dumps(result_json(result))
    assert SENTINEL not in caplog.text


def test_a_rejected_argument_key_says_where_it_came_from_but_not_what_it_is(
    monkeypatch: pytest.MonkeyPatch,
):
    rejection = httpx2.Response(
        401, json=api_error("authentication_error", "invalid x-api-key")
    )
    fake_anthropic(monkeypatch, lambda prompt: rejection)

    with pytest.raises(APIKeyRejectedError) as error:
        evaluate_sync(SOURCE, SUMMARY, anthropic_api_key=SENTINEL)

    message = str(error.value)
    assert message.startswith("The Anthropic API key was rejected")
    assert "Key from: the anthropic_api_key argument" in message
    assert SENTINEL not in message
    assert SENTINEL not in error.value.api_message()


def test_the_client_hides_its_key_when_printed():
    client = AnthropicClient(SENTINEL, key_source="the anthropic_api_key argument")

    assert SENTINEL not in repr(client)
    assert SENTINEL not in str(client)
