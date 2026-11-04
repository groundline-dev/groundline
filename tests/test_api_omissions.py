import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from groundline import load_preset
from groundline.api import omissions as api
from groundline.app import app
from groundline.core import APIKeyRejectedError
from groundline.evaluate import DEFAULT_PRESET
from tests.auth_helpers import SECRET as API_KEY
from tests.auth_helpers import FakeKeys
from tests.fakes import FakeClient
from tests.omission_helpers import (
    EXTRACTED,
    SOURCE,
    SUMMARY,
    Script,
    flagged_script,
    not_flagged_script,
    step_of,
)

http = TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})

SECRET = "patient is HIV positive and takes tenofovir"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No real .env, and no client override left behind."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


def use_client(replies: Callable[[str], str] | None) -> FakeClient | None:
    client = FakeClient(replies) if replies else None
    app.dependency_overrides[api.model_client] = lambda: client
    return client


def check(**body: object) -> dict[str, object]:
    return {"source": SOURCE, "summary": SUMMARY, **body}


def test_a_summary_that_is_not_flagged():
    use_client(not_flagged_script())

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 200
    body = response.json()
    assert body["flagged"] is False
    assert body["alarm"] == {"score": 0.0, "threshold": 0.9, "level": "strict"}
    assert body["pipeline"] == body["validated_preset"] == DEFAULT_PRESET
    assert body["counts"] == {"total": 3, "present": 1, "partial": 1, "absent": 1}
    assert body["usage"]["calls"] == 4


def test_a_flagged_summary_still_answers_200():
    use_client(flagged_script())

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 200
    body = response.json()
    assert body["flagged"] is True
    assert body["omissions"][0] == {
        "fact": "Motrin 600 mg every 6 hours for a week.",
        "severity": "critical",
        "verdict": "absent",
        "missing_confidence": 0.95,
        "quote": None,
    }


def test_the_moderate_threshold_is_still_the_validated_preset():
    use_client(not_flagged_script())

    body = http.post("/v1/omissions", json=check(threshold="moderate")).json()

    assert body["alarm"]["level"] == "moderate"
    assert body["alarm"]["threshold"] == 0.425
    assert body["validated_preset"] == DEFAULT_PRESET


def test_the_threshold_levels_are_the_presets():
    alarm = next(s for s in load_preset(DEFAULT_PRESET).stages if s.step == "alarm")

    assert set(api.ThresholdLevel.__args__) == set(alarm.settings["thresholds"])


@pytest.mark.parametrize(
    "body",
    [
        check(threshold="extreme"),
        check(source=""),
        check(summary="  \n "),
        check(source="x" * (api.MAX_CHARS + 1)),
        check(pipeline="custom.yaml"),
        {"source": SOURCE},
    ],
    ids=["unknown threshold", "empty", "blank", "too long", "extra field", "missing"],
)
def test_unusable_input_is_a_422_before_any_call(body: dict[str, object]):
    client = use_client(lambda prompt: "unused")

    response = http.post("/v1/omissions", json=body)

    assert response.status_code == 422
    assert client is not None and client.requests == []


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        (check(source=SECRET + "x" * api.MAX_CHARS), ["body", "source"]),
        (check(source={"text": SECRET}), ["body", "source"]),
        (check(threshold=SECRET), ["body", "threshold"]),
        (check(notes=SECRET), ["body"]),
        (check(**{SECRET: "x"}), ["body"]),
    ],
    ids=["too long", "not text", "unknown threshold", "extra value", "extra name"],
)
def test_a_422_never_repeats_the_input(body: dict[str, object], loc: list[str]):
    use_client(not_flagged_script())

    response = http.post("/v1/omissions", json=body)

    assert response.status_code == 422
    assert "tenofovir" not in response.text
    assert response.json()["detail"][0]["loc"] == loc


def test_a_missing_key_is_a_503_with_the_clis_message(
    monkeypatch: pytest.MonkeyPatch,
):
    use_client(None)  # the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 503
    assert response.json()["detail"].startswith("ANTHROPIC_API_KEY is not set.")


def test_a_rejected_key_is_a_503():
    def rejected(prompt: str) -> str:
        raise APIKeyRejectedError("ANTHROPIC_API_KEY was rejected")

    use_client(rejected)

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 503
    assert response.json()["detail"] == "ANTHROPIC_API_KEY was rejected"


def test_an_unusable_reply_is_a_502_naming_the_stage_and_cost():
    use_client(Script(extract="not json"))

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail.startswith("stage 'extract' failed: unusable reply after 1 retry")
    assert "Cost before stopping: $0." in detail and "· 2 calls" in detail


def _api_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            anthropic.BadRequestError(
                f"Error code: 400 - {SECRET}",
                response=httpx2.Response(400, request=_api_request()),
                body={"error": {"message": SECRET}},
            ),
            "stage 'extract' failed: the Anthropic API answered 400 (BadRequestError)",
        ),
        (
            anthropic.APIConnectionError(message=SECRET, request=_api_request()),
            "stage 'extract' failed: the Anthropic API failed (APIConnectionError)",
        ),
    ],
    ids=["status", "connection"],
)
def test_an_anthropic_api_error_is_a_502_without_its_message(
    error: anthropic.APIError, detail: str, caplog: pytest.LogCaptureFixture
):
    def failing(prompt: str) -> str:
        raise error

    use_client(failing)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/omissions", json=check())

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    assert type(error).__name__ in caplog.text
    assert "tenofovir" not in caplog.text


def test_an_anthropic_api_error_after_some_calls_says_what_they_cost():
    script = Script(extract=EXTRACTED)

    def failing_at_rank(prompt: str) -> str:
        if step_of(prompt) == "rank":
            raise anthropic.InternalServerError(
                SECRET,
                response=httpx2.Response(500, request=_api_request()),
                body=None,
            )
        return script(prompt)

    use_client(failing_at_rank)

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail.startswith(
        "stage 'rank' failed: the Anthropic API answered 500 (InternalServerError)"
        ". Cost before stopping: $0."
    )
    assert detail.endswith("· 1 calls")
    assert "tenofovir" not in detail


def test_a_stage_failure_of_our_own_is_a_generic_500(
    caplog: pytest.LogCaptureFixture,
):
    def broken(prompt: str) -> str:
        raise ValueError(SECRET)

    use_client(broken)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/omissions", json=check())

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert "stage 'extract' failed" in caplog.text
    assert "ValueError" in caplog.text and "StageError" in caplog.text
    assert "tenofovir" not in response.text
    assert "tenofovir" not in caplog.text


async def _evaluate_fails(*args: object, **kwargs: object) -> None:
    raise KeyError(SECRET)


def _preset_fails(name: str) -> None:
    raise ValueError(SECRET)


def _response_fails(**fields: object) -> None:
    raise ValueError(SECRET)


@pytest.mark.parametrize(
    "target, broken, error",
    [
        ("evaluate", _evaluate_fails, "KeyError"),
        ("load_preset", _preset_fails, "ValueError"),
        ("Counts", _response_fails, "ValueError"),
    ],
    ids=["while checking", "loading the preset", "building the response"],
)
def test_an_unexpected_error_is_a_generic_500(
    target: str,
    broken: Callable[..., object],
    error: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    use_client(not_flagged_script())
    monkeypatch.setattr(api, target, broken)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/omissions", json=check())

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert f"unexpected {error}" in caplog.text
    assert f"in {broken.__name__}" in caplog.text  # where it happened
    assert "tenofovir" not in response.text
    assert "tenofovir" not in caplog.text


def test_startup_warns_when_the_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with caplog.at_level(logging.WARNING), TestClient(app):
        pass

    assert "ANTHROPIC_API_KEY is not set, so checks will answer 503" in caplog.text


def test_startup_is_quiet_when_the_key_is_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    with caplog.at_level(logging.WARNING), TestClient(app):
        pass

    assert "ANTHROPIC_API_KEY" not in caplog.text


def test_startup_warns_when_the_settings_cant_be_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    (tmp_path / ".env").write_bytes(b"ANTHROPIC_API_KEY=\xff\xfe" + SECRET.encode())

    with caplog.at_level(logging.WARNING), TestClient(app):
        pass

    assert "can't read the settings (UnicodeDecodeError)." in caplog.text
    assert "Checks will answer 503 until it's fixed." in caplog.text
    assert "tenofovir" not in caplog.text


def test_unreadable_settings_are_a_503_without_their_text(tmp_path: Path):
    (tmp_path / ".env").write_bytes(b"ANTHROPIC_API_KEY=\xff\xfe" + SECRET.encode())
    use_client(None)  # the real Anthropic client, which needs the settings

    response = http.post("/v1/omissions", json=check())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("can't read the settings (UnicodeDecodeError).")
    assert "tenofovir" not in detail


def test_a_finished_check_is_logged_without_its_input(
    caplog: pytest.LogCaptureFixture,
):
    use_client(flagged_script())

    with caplog.at_level(logging.DEBUG):
        http.post("/v1/omissions", json=check(source=SOURCE + "\n" + SECRET))

    (line,) = [r.getMessage() for r in caplog.records if "checked" in r.getMessage()]
    assert line.startswith("checked a summary: FLAGGED (alarm 0.95 ≥ 0.90, strict)")
    assert "· 3 omissions · $0." in line and "4 calls" in line
    assert "Motrin" not in line
    assert "tenofovir" not in caplog.text  # at any level, from any logger
