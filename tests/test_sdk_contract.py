"""The SDK against the real app, so an API change that breaks the SDK fails CI.

The real app runs under uvicorn on a loopback port in this process, and the real
`GroundlineClient` calls it over HTTP. Only the model (a fake Anthropic service)
and the API-key storage are faked. The SDK's own tests, which use a fake server
built from openapi.json, can't see a route or field that the app changed and the
file didn't.
"""

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import uvicorn
from groundline_client import (
    AuthenticationError,
    CommissionsRequest,
    GroundlineClient,
    InvalidRequestError,
    OmissionsRequest,
    ServerUnavailableError,
)

from groundline_core.app import app
from tests.anthropic_helpers import fake_anthropic
from tests.api_integration_helpers import isolated as isolated
from tests.api_integration_helpers import keys as keys
from tests.auth_helpers import SECRET, FakeKeys
from tests.commissions_helpers import (
    DOSE_ISSUE,
    EXAM_ISSUE,
    issues_reply,
)
from tests.commissions_helpers import SUMMARY as COMMISSION_SUMMARY
from tests.commissions_helpers import Script as CommissionScript
from tests.disclaimer_helpers import clinical_disclaimer
from tests.omission_helpers import (
    SOURCE,
    SUMMARY,
    flagged_script,
    not_flagged_script,
)

WRONG_KEY = "gl_" + "b" * 43


class FakeDatabase:
    """Stands in for the app's database check, which `/ready` reports."""

    def __init__(self) -> None:
        self.answers = True

    async def check(self) -> bool:
        return self.answers


@pytest.fixture
def api_url(
    isolated: set[tuple[str, int]], keys: FakeKeys, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """The real app, listening on a free loopback port until the test ends."""

    assert app.state.api_keys is keys, "the server must check keys against the fake"
    monkeypatch.setattr(app.state, "database", FakeDatabase(), raising=False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        isolated.add(("127.0.0.1", port))
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive(), "the app's server stopped before it was ready"
            assert time.monotonic() < deadline, "the app's server did not start"
            time.sleep(0.01)
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def call_through_client(
    api_url: str,
    method: str,
    *arguments: Any,
    is_async: bool,
    api_key: str = SECRET,
    **options: Any,
) -> Any:
    """Make one call the way the guide shows it: a sync call inside `with`, an
    async one inside `async with`. Any error is raised to the caller."""
    client = GroundlineClient(base_url=api_url, api_key=api_key, **options)
    if not is_async:
        with client:
            return getattr(client, method)(*arguments)

    async def run() -> Any:
        async with client:
            return await getattr(client, f"{method}_async")(*arguments)

    return asyncio.run(run())


@dataclass(frozen=True)
class CheckCase:
    """One check endpoint as the SDK calls it, with a model script that flags
    the summary and one that doesn't."""

    method: str
    request_for: Callable[[str], Any]
    """Builds the request from a source text, paired with the fixed summary."""
    flagged_script: Callable[[], Callable[[str], str]]
    not_flagged_script: Callable[[], Callable[[str], str]]
    pipeline: str
    flagged_finding_count: int
    clear_finding_count: int


CHECK_CASES = [
    pytest.param(
        CheckCase(
            method="evaluate_omissions",
            request_for=lambda source: OmissionsRequest(source=source, summary=SUMMARY),
            flagged_script=flagged_script,
            not_flagged_script=not_flagged_script,
            pipeline="omission_all_opus",
            flagged_finding_count=3,
            clear_finding_count=2,
        ),
        id="omissions",
    ),
    pytest.param(
        CheckCase(
            method="evaluate_commissions",
            request_for=lambda source: CommissionsRequest(
                source=source, summary=COMMISSION_SUMMARY
            ),
            flagged_script=lambda: CommissionScript(
                commissions_check=issues_reply(DOSE_ISSUE, EXAM_ISSUE)
            ),
            not_flagged_script=lambda: CommissionScript(
                commissions_check=issues_reply()
            ),
            pipeline="commission_quick",
            flagged_finding_count=2,
            clear_finding_count=0,
        ),
        id="commissions",
    ),
]

BOTH_WAYS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


@pytest.mark.parametrize("check", CHECK_CASES)
@BOTH_WAYS
def test_a_flagged_summary_comes_back_as_a_result(
    check: CheckCase,
    is_async: bool,
    api_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    sent = fake_anthropic(monkeypatch, check.flagged_script())

    result = call_through_client(
        api_url, check.method, check.request_for(SOURCE), is_async=is_async
    )

    assert result.flagged is True
    assert result.score < 0.5
    assert len(result.findings) == check.flagged_finding_count
    assert result.findings[0].statement
    assert result.metadata.pipeline == check.pipeline
    assert result.metadata.alarm.score > 0
    assert result.metadata.usage.calls == len(sent) > 0
    assert result.metadata.disclaimer == clinical_disclaimer()


@pytest.mark.parametrize("check", CHECK_CASES)
@BOTH_WAYS
def test_a_summary_that_is_not_flagged_comes_back_as_a_result(
    check: CheckCase,
    is_async: bool,
    api_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    fake_anthropic(monkeypatch, check.not_flagged_script())

    result = call_through_client(
        api_url, check.method, check.request_for(SOURCE), is_async=is_async
    )

    assert result.flagged is False
    assert result.score == 1.0
    assert len(result.findings) == check.clear_finding_count
    assert result.metadata.alarm.score == 0.0


@pytest.mark.parametrize("method", ["health", "ready"])
@BOTH_WAYS
def test_the_probes_report_ok(method: str, is_async: bool, api_url: str):
    result = call_through_client(api_url, method, is_async=is_async)

    assert result.additional_properties == {"status": "ok"}


def test_ready_raises_a_server_error_when_the_database_is_down(api_url: str):
    app.state.database.answers = False

    with pytest.raises(ServerUnavailableError) as raised:
        call_through_client(api_url, "ready", is_async=False, max_attempts=1)

    assert raised.value.status_code == 503


@pytest.mark.parametrize("check", CHECK_CASES)
@BOTH_WAYS
def test_a_wrong_api_key_raises_an_authentication_error_before_any_model_call(
    check: CheckCase,
    is_async: bool,
    api_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    sent = fake_anthropic(monkeypatch, check.flagged_script())

    with pytest.raises(AuthenticationError) as raised:
        call_through_client(
            api_url,
            check.method,
            check.request_for(SOURCE),
            is_async=is_async,
            api_key=WRONG_KEY,
        )

    assert raised.value.status_code == 401
    assert sent == []


@pytest.mark.parametrize("check", CHECK_CASES)
@BOTH_WAYS
def test_a_blank_source_raises_an_invalid_request_error_that_says_where(
    check: CheckCase,
    is_async: bool,
    api_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    sent = fake_anthropic(monkeypatch, check.not_flagged_script())

    with pytest.raises(InvalidRequestError) as raised:
        call_through_client(
            api_url, check.method, check.request_for(" "), is_async=is_async
        )

    assert raised.value.status_code == 422
    assert raised.value.detail is not None
    assert "source" in raised.value.detail
    assert sent == []
