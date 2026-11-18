import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import get_args

import httpx2
import pytest
from fastapi.testclient import TestClient

from groundline_core import builtin_presets, load_preset
from groundline_core._core import APIKeyRejectedError
from groundline_core._evaluate import DEFAULT_COMMISSIONS_PRESET

# The route's module, imported as `api` because the tests patch the names it looks up
# (evaluate, load_preset and the response models) there, not in `common`.
from groundline_core.api import commissions as api
from groundline_core.api import common
from groundline_core.app import app
from tests.anthropic_helpers import (
    bad_request,
    unreachable,
    use_real_anthropic,
)
from tests.auth_helpers import HEADERS, FakeKeys
from tests.commissions_helpers import (
    DOSE,
    DOSE_CONTRADICTED,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    EXAM_UNSUPPORTED,
    ISSUES,
    SKIING_SUPPORTED,
    SUMMARY,
    Script,
    issue,
    issues_reply,
    lean_script,
    verdicts_reply,
)
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE

http = TestClient(app, headers=HEADERS)

SECRET = "patient is HIV positive and takes tenofovir"
MAX_SOURCE_CHARS = common.LIMITS.max_source_chars


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No real .env, and no client override left behind."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


def use_client(replies: Callable[[str], str] | None) -> FakeClient | None:
    client = FakeClient(replies) if replies else None
    app.dependency_overrides[common.model_client] = lambda: client
    return client


def check(**body: object) -> dict[str, object]:
    return {"source": SOURCE, "summary": SUMMARY, **body}


def test_a_flagged_summary_answers_200_with_its_commissions_most_important_first():
    use_client(Script(commissions_check=issues_reply(EXAM_ISSUE, DOSE_ISSUE)))

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 200
    body = response.json()
    assert body["evaluation_type"] == "commissions"
    assert body["flagged"] is True
    assert body["score"] == pytest.approx(0.1667, abs=1e-4)
    assert body["counts"] is None
    metadata = body["metadata"]
    assert metadata.pop("usage")["calls"] == 1
    assert metadata == {
        "pipeline": DEFAULT_COMMISSIONS_PRESET,
        "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
    }
    assert [(f["statement"], f["verdict"]) for f in body["findings"]] == [
        (DOSE, "contradicted"),
        (EXAM, "unsupported"),
    ]
    assert body["findings"][0]["source_evidence"] == DOSE_SOURCE


def test_the_response_has_only_the_documented_fields():
    use_client(Script(commissions_check=ISSUES))

    body = http.post("/v1/commissions", json=check()).json()

    assert set(body) == {
        "evaluation_type",
        "flagged",
        "score",
        "findings",
        "counts",
        "metadata",
    }
    assert set(body["metadata"]) == {"pipeline", "alarm", "usage"}
    assert set(body["findings"][0]) == {
        "type",
        "severity",
        "verdict",
        "confidence",
        "statement",
        "source_evidence",
        "summary_evidence",
        "evidence_found",
        "explanation",
    }


def test_a_summary_with_nothing_unsupported_is_not_flagged():
    use_client(Script(commissions_check=issues_reply()))

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 200
    body = response.json()
    assert body["flagged"] is False
    assert body["metadata"]["alarm"] == {
        "score": 0.0,
        "threshold": 0.85,
        "level": "default",
    }
    assert body["score"] == 1.0
    assert body["findings"] == []


def test_a_commission_that_isnt_critical_is_listed_but_doesnt_flag():
    use_client(Script(commissions_check=issues_reply(EXAM_ISSUE)))

    body = http.post("/v1/commissions", json=check()).json()

    assert body["flagged"] is False
    assert [f["statement"] for f in body["findings"]] == [EXAM]


def test_a_quote_that_skips_the_scribes_tags_is_listed_but_not_found():
    tagged = SOURCE.replace("600 mg", "<UNSURE> 600 </UNSURE> mg")
    use_client(Script(commissions_check=issues_reply(DOSE_ISSUE)))

    response = http.post("/v1/commissions", json=check(source=tagged))

    assert response.status_code == 200
    (found,) = response.json()["findings"]
    assert found["source_evidence"] == DOSE_SOURCE
    assert found["evidence_found"] is False


def test_a_quote_that_isnt_in_the_source_at_all_is_not_found():
    reworded = issue(DOSE, "contradicted", 95, "critical", "take ibuprofen daily")
    use_client(Script(commissions_check=issues_reply(reworded)))

    (found,) = http.post("/v1/commissions", json=check()).json()["findings"]

    assert found["evidence_found"] is False


def test_each_endpoint_describes_its_own_alarm_score():
    schemas = app.openapi()["components"]["schemas"]

    omissions = schemas["OmissionsAlarm"]["properties"]["score"]["description"]
    commissions = schemas["CommissionsAlarm"]["properties"]["score"]["description"]

    assert "half weight" in omissions
    assert "commissions" in commissions and "half weight" not in commissions


def test_the_route_runs_the_quick_preset():
    use_client(Script(commissions_check=issues_reply()))

    body = http.post("/v1/commissions", json=check()).json()

    assert body["metadata"]["pipeline"] == load_preset("commission_quick").name


@pytest.mark.parametrize(
    "body",
    [
        check(threshold="default"),
        check(source=""),
        check(summary="  \n "),
        check(source="x" * (MAX_SOURCE_CHARS + 1)),
        check(pipeline="custom.yaml"),
        {"source": SOURCE},
    ],
    ids=["threshold", "empty", "blank", "too long", "extra field", "missing"],
)
def test_unusable_input_is_a_422_before_any_call(body: dict[str, object]):
    client = use_client(lambda prompt: "unused")

    response = http.post("/v1/commissions", json=body)

    assert response.status_code == 422
    assert client is not None and client.requests == []


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        (
            check(source=SECRET + "x" * MAX_SOURCE_CHARS),
            ["body", "source"],
        ),
        (check(source={"text": SECRET}), ["body", "source"]),
        (check(notes=SECRET), ["body"]),
        (check(**{SECRET: "x"}), ["body"]),
    ],
    ids=["too long", "not text", "extra value", "extra name"],
)
def test_a_422_never_repeats_the_input(body: dict[str, object], loc: list[str]):
    use_client(Script(commissions_check=issues_reply()))

    response = http.post("/v1/commissions", json=body)

    assert response.status_code == 422
    assert "tenofovir" not in response.text
    assert response.json()["detail"][0]["loc"] == loc


def test_a_missing_key_is_a_503_with_the_clis_message(
    monkeypatch: pytest.MonkeyPatch,
):
    use_client(None)  # the real Anthropic client
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("ANTHROPIC_API_KEY is not set.")
    assert "claude-haiku-5-5" in detail  # says which model needs it
    assert str(Path.cwd()) not in detail  # no path of the server's


def test_a_rejected_key_is_a_503():
    def rejected(prompt: str) -> str:
        raise APIKeyRejectedError("ANTHROPIC_API_KEY was rejected")

    use_client(rejected)

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 503
    assert response.json()["detail"] == "ANTHROPIC_API_KEY was rejected"


def test_unreadable_settings_are_a_503_without_their_text(tmp_path: Path):
    (tmp_path / ".env").write_bytes(b"ANTHROPIC_API_KEY=\xff\xfe" + SECRET.encode())
    use_client(None)  # the real Anthropic client, which needs the settings

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("can't read the settings (UnicodeDecodeError).")
    assert "tenofovir" not in detail


def test_an_unusable_reply_is_a_502_naming_the_stage_and_cost():
    use_client(Script(commissions_check="not json"))

    response = http.post("/v1/commissions", json=check())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail.startswith(
        "stage 'commissions_check' failed: The model’s reply remained unusable "
        "after a request for correction"
    )
    assert "Recorded cost before stopping: $0." in detail and "· 2 calls" in detail


@pytest.mark.parametrize(
    ("reply", "detail", "cause"),
    [
        (
            bad_request(SECRET),
            "stage 'commissions_check' failed: "
            "the Anthropic API answered 400 (BadRequestError)",
            "BadRequestError",
        ),
        (
            unreachable(SECRET),
            "stage 'commissions_check' failed: "
            "the Anthropic API failed (APIConnectionError)",
            "APIConnectionError",
        ),
    ],
    ids=["status", "connection"],
)
def test_an_anthropic_api_error_is_a_502_without_its_message(
    reply: Callable[[str], httpx2.Response],
    detail: str,
    cause: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """The real Anthropic client, on a fake transport."""
    use_client(None)
    use_real_anthropic(monkeypatch, reply)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/commissions", json=check())

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    assert cause in caplog.text
    assert "tenofovir" not in caplog.text


def test_a_stage_failure_of_our_own_is_a_generic_500(
    caplog: pytest.LogCaptureFixture,
):
    def broken(prompt: str) -> str:
        raise ValueError(SECRET)

    use_client(broken)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/commissions", json=check())

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert "unexpected ValueError" in caplog.text
    assert "tenofovir" not in response.text
    assert "tenofovir" not in caplog.text


async def _evaluate_fails(*args: object, **kwargs: object) -> None:
    raise KeyError(SECRET)


def _preset_fails(name: str) -> None:
    raise ValueError(SECRET)


def _response_fails(*args: object, **fields: object) -> None:
    raise ValueError(SECRET)


@pytest.mark.parametrize(
    "target, broken, error",
    [
        ("evaluate", _evaluate_fails, "KeyError"),
        ("load_preset", _preset_fails, "ValueError"),
        ("alarm_of", _response_fails, "ValueError"),
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
    use_client(Script(commissions_check=ISSUES))
    monkeypatch.setattr(api, target, broken)

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/commissions", json=check())

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert f"unexpected {error}" in caplog.text
    assert f"in {broken.__name__}" in caplog.text  # where it happened
    assert "tenofovir" not in response.text
    assert "tenofovir" not in caplog.text


def test_a_finished_check_is_logged_without_its_input(
    caplog: pytest.LogCaptureFixture,
):
    use_client(Script(commissions_check=ISSUES))

    with caplog.at_level(logging.DEBUG):
        http.post("/v1/commissions", json=check(source=SOURCE + "\n" + SECRET))

    (line,) = [r.getMessage() for r in caplog.records if "checked" in r.getMessage()]
    assert line.startswith("checked a summary: FLAGGED (alarm 0.95 ≥ 0.85, default)")
    assert "· 2 commissions · $0." in line and "1 calls" in line
    assert "Motrin" not in line
    assert "tenofovir" not in caplog.text  # at any level, from any logger


# --- choosing a preset -----------------------------------------------------------

LEAN = "commission_reverse_lean"


def test_choosing_quick_by_name_is_the_same_as_leaving_preset_out():
    use_client(Script(commissions_check=ISSUES))
    left_out = http.post("/v1/commissions", json=check()).json()
    use_client(Script(commissions_check=ISSUES))
    named = http.post("/v1/commissions", json=check(preset="commission_quick")).json()

    assert named == left_out
    assert named["metadata"]["pipeline"] == "commission_quick"


def test_the_lean_preset_answers_in_the_same_shape_with_its_own_metadata():
    client = use_client(lean_script())

    response = http.post("/v1/commissions", json=check(preset=LEAN))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "evaluation_type",
        "flagged",
        "score",
        "findings",
        "counts",
        "metadata",
    }
    assert (body["evaluation_type"], body["flagged"], body["counts"]) == (
        "commissions",
        True,
        None,
    )
    metadata = body["metadata"]
    assert set(metadata) == {"pipeline", "alarm", "usage"}
    assert metadata["pipeline"] == LEAN
    assert metadata["alarm"] == {"score": 0.95, "threshold": 0.8, "level": "default"}
    assert metadata["usage"]["calls"] == 3
    assert client is not None
    assert {r.model for r in client.requests} == {"claude-opus-5-5"}
    # A finding's statement is the summary's own words, as with quick.
    assert [(f["statement"], f["verdict"]) for f in body["findings"]] == [
        (DOSE, "contradicted"),
        (EXAM, "unsupported"),
    ]
    assert body["findings"][0]["source_evidence"] == DOSE_SOURCE


@pytest.mark.parametrize(
    ("preset", "flagged", "score"),
    [("commission_quick", False, 0.5176), (LEAN, True, 0.45)],
)
def test_the_score_is_rescaled_around_the_presets_own_threshold(
    preset: str, flagged: bool, score: float
):
    # The same 0.82 alarm score passes quick's 0.85 but reaches lean's 0.80.
    if preset == LEAN:
        dose = {**DOSE_CONTRADICTED, "not_supported_confidence": 82}
        use_client(
            lean_script(verdicts_reply(SKIING_SUPPORTED, EXAM_UNSUPPORTED, dose))
        )
    else:
        dose = issue(DOSE, "contradicted", 82, "critical", DOSE_SOURCE)
        use_client(Script(commissions_check=issues_reply(dose)))

    body = http.post("/v1/commissions", json=check(preset=preset)).json()

    assert body["metadata"]["alarm"]["score"] == 0.82
    assert body["flagged"] is flagged
    assert body["score"] == pytest.approx(score, abs=1e-4)


@pytest.mark.parametrize(
    "preset",
    [SECRET, "commission_exact", None, 1],
    ids=["unknown", "a preset left out", "null", "not text"],
)
def test_an_unknown_preset_is_a_422_listing_the_allowed_values(preset: object):
    client = use_client(lambda prompt: "unused")

    response = http.post("/v1/commissions", json=check(preset=preset))

    assert response.status_code == 422
    assert client is not None and client.requests == []
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "preset"]
    assert "'commission_quick'" in problem["msg"]
    assert "'commission_reverse_lean'" in problem["msg"]
    assert "tenofovir" not in response.text


def test_the_choosable_presets_are_exactly_the_built_in_commission_presets():
    choosable = set(get_args(api.CommissionPreset))

    assert choosable == {p for p in builtin_presets() if p.startswith("commission_")}
