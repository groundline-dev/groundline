"""What a client method raises for each answer, sync and async (ALL-158).

Every case goes through `GroundlineClient` and the fake server, so no test
reaches the real API. `max_attempts=1` keeps a retryable status (408, 429, 5xx)
from waiting for a real backoff: the retry rules have their own tests.
"""

import asyncio
from typing import Any

import groundline_client as sdk
import pytest
from conftest import FakeServer, Step

SYNC_AND_ASYNC = pytest.mark.parametrize(
    "is_async", [False, True], ids=["sync", "async"]
)
OPERATIONS = [
    ("evaluate_omissions", "POST", "/v1/omissions", "OmissionsRequest"),
    ("evaluate_commissions", "POST", "/v1/commissions", "CommissionsRequest"),
    ("health", "GET", "/health", None),
    ("ready", "GET", "/ready", None),
]
OMISSIONS = OPERATIONS[0]


def make_client(fake_server: FakeServer, api_key: str, **options: Any) -> Any:
    options.setdefault("max_attempts", 1)
    return sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key, **options)


def call(
    client: Any,
    operation: tuple[str, str, str, str | None],
    server: FakeServer,
    is_async: bool,
) -> Any:
    name, _, _, request_model = operation
    arguments: list[Any] = []
    if request_model is not None:
        arguments.append(
            getattr(sdk, request_model).from_dict(server.examples[request_model])
        )
    if is_async:
        return asyncio.run(getattr(client, f"{name}_async")(*arguments))
    return getattr(client, name)(*arguments)


def raised_by(
    fake_server: FakeServer,
    api_key: str,
    is_async: bool,
    status: int,
    body: Any = None,
    headers: dict[str, str] | None = None,
    operation: tuple[str, str, str, str | None] = OMISSIONS,
) -> sdk.GroundlineError:
    """The error a call raises when the route answers like this."""
    _, method, path, _ = operation
    fake_server.reply(
        method, path, status, {"detail": "x"} if body is None else body, headers
    )
    with pytest.raises(sdk.GroundlineError) as raised:
        call(make_client(fake_server, api_key), operation, fake_server, is_async)
    return raised.value


# R2 (status maps to subclass)


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, sdk.InvalidRequestError),
        (401, sdk.AuthenticationError),
        (403, sdk.AuthenticationError),
        (404, sdk.GroundlineError),
        (408, sdk.GroundlineTimeoutError),
        (413, sdk.RequestTooLargeError),
        (418, sdk.GroundlineError),
        (422, sdk.InvalidRequestError),
        (429, sdk.RateLimitError),
        (500, sdk.ServerUnavailableError),
        (502, sdk.ServerUnavailableError),
        (503, sdk.ServerUnavailableError),
        (504, sdk.GroundlineTimeoutError),
    ],
)
def test_each_status_raises_its_class(
    status: int,
    expected: type[sdk.GroundlineError],
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
):
    error = raised_by(fake_server, api_key, is_async, status)

    assert type(error) is expected
    assert error.status_code == status
    assert error.detail == "x"


@SYNC_AND_ASYNC
@pytest.mark.parametrize(("name", "method", "path", "model"), OPERATIONS)
def test_every_route_raises_instead_of_returning_an_error(
    name: str,
    method: str,
    path: str,
    model: str | None,
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
):
    error = raised_by(
        fake_server, api_key, is_async, 401, operation=(name, method, path, model)
    )

    assert type(error) is sdk.AuthenticationError


@SYNC_AND_ASYNC
def test_a_422_lists_its_problems_in_detail(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    problem = {"loc": ["body", "source"], "msg": "Field required", "type": "missing"}

    error = raised_by(fake_server, api_key, is_async, 422, {"detail": [problem]})

    assert type(error) is sdk.InvalidRequestError
    assert error.detail == "body.source: Field required"


@SYNC_AND_ASYNC
def test_a_rate_limit_error_carries_the_retry_after_of_the_last_answer(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(fake_server, api_key, is_async, 429, headers={"Retry-After": "7"})

    assert isinstance(error, sdk.RateLimitError)
    assert error.retry_after == 7.0


@SYNC_AND_ASYNC
def test_the_error_is_raised_only_after_the_retries_run_out(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.script(
        "GET", "/health", [Step(503, {"detail": "busy"}), Step(200, {"status": "ok"})]
    )

    result = call(
        make_client(fake_server, api_key, max_attempts=2),
        ("health", "GET", "/health", None),
        fake_server,
        is_async,
    )

    assert result.to_dict() == {"status": "ok"}
    assert len(fake_server.requests) == 2


# R4 (malformed responses): never a raw crash


@SYNC_AND_ASYNC
@pytest.mark.parametrize("status", [502, 503])
def test_a_proxys_html_error_page_raises_a_server_unavailable_error(
    status: int, is_async: bool, fake_server: FakeServer, api_key: str
):
    page = b"<html><body><h1>Bad gateway</h1></body></html>"

    error = raised_by(fake_server, api_key, is_async, status, page)

    assert type(error) is sdk.ServerUnavailableError
    assert error.detail is None
    assert "Bad gateway" not in str(error)


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    "body",
    [b"{broken", b"", [1, 2], {"detail": 5}, {"other": "field"}],
    ids=["broken-json", "empty", "list", "number-detail", "no-detail"],
)
def test_an_error_body_of_the_wrong_shape_still_gives_the_class_for_its_status(
    body: Any, is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(fake_server, api_key, is_async, 401, body)

    assert type(error) is sdk.AuthenticationError
    assert error.detail is None


@SYNC_AND_ASYNC
def test_a_redirect_raises_a_plain_error_and_is_not_followed(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(
        fake_server,
        api_key,
        is_async,
        302,
        b"",
        {"Location": f"{fake_server.url}/elsewhere"},
    )

    assert type(error) is sdk.GroundlineError
    assert error.status_code == 302
    assert len(fake_server.requests) == 1


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    "body",
    [b"<html>maintenance</html>", b"", {"unexpected": "shape"}],
    ids=["html", "empty", "wrong-shape"],
)
def test_a_success_status_with_a_body_that_cannot_be_read_raises_a_plain_error(
    body: Any, is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(fake_server, api_key, is_async, 200, body)

    assert type(error) is sdk.GroundlineError
    assert "maintenance" not in str(error)


@SYNC_AND_ASYNC
@pytest.mark.parametrize("status", [201, 202])
def test_a_success_status_the_api_does_not_document_raises_a_plain_error(
    status: int, is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(fake_server, api_key, is_async, status, {"status": "ok"})

    assert type(error) is sdk.GroundlineError


@SYNC_AND_ASYNC
def test_a_success_status_with_absurdly_nested_json_raises_a_plain_error(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    error = raised_by(fake_server, api_key, is_async, 200, b"[" * 200_000)

    assert type(error) is sdk.GroundlineError


# A flagged summary is a result (R3): see test_client_retries.py,
# test_a_flagged_summary_is_a_normal_result_and_is_not_retried.


# Failures with no answer


@SYNC_AND_ASYNC
def test_a_refused_connection_raises_a_server_unavailable_error(
    is_async: bool, api_key: str, closed_port: int
):
    client: Any = sdk.GroundlineClient(
        base_url=f"http://127.0.0.1:{closed_port}", api_key=api_key, max_attempts=1
    )

    with pytest.raises(sdk.GroundlineError) as raised:
        if is_async:
            asyncio.run(client.health_async())
        else:
            client.health()

    assert type(raised.value) is sdk.ServerUnavailableError
    assert raised.value.status_code is None
    assert raised.value.detail is None


@SYNC_AND_ASYNC
def test_a_call_that_hangs_raises_a_timeout_error(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.hang("GET", "/health")
    client = make_client(fake_server, api_key, timeout_seconds=0.3)

    with pytest.raises(sdk.GroundlineError) as raised:
        call(client, ("health", "GET", "/health", None), fake_server, is_async)

    assert type(raised.value) is sdk.GroundlineTimeoutError
    assert raised.value.status_code is None
