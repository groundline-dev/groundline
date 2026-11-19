"""No SDK error shows the API key, source or summary text, or the request.

An `httpx2` error carries its request, and the request's headers hold the API
key, so the SDK drops the original error instead of chaining it. Each test
looks at everything a tool could print or walk: the traceback with local
variables, `repr`, `args`, `vars()` and both exception chains.
"""

import asyncio
import traceback
from typing import Any

import groundline_client as sdk
import pytest
from conftest import FakeServer

# Module-level, not the `api_key` fixture: a fixture value would be a local
# variable of the test, and show up in its own traceback.
SECRET_KEY = "test-key-not-a-real-secret"  # betterleaks:allow a dummy value
SOURCE_TEXT = "PATIENT-SOURCE-TEXT-123"
SYNC_AND_ASYNC = pytest.mark.parametrize(
    "is_async", [False, True], ids=["sync", "async"]
)


def everything_visible_about(error: BaseException) -> str:
    """All the text a tool could get from the error, including local variables."""
    formatted = "".join(
        traceback.TracebackException.from_exception(error, capture_locals=True).format()
    )
    return "\n".join(
        [formatted, str(error), repr(error), repr(error.args), repr(vars(error))]
    )


def assert_safe(error: BaseException) -> None:
    assert SECRET_KEY not in everything_visible_about(error)
    # Nothing is chained, so `error.request.headers` can't be reached.
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not hasattr(error, "request")
    assert not hasattr(error, "response")


def trigger(client: Any, operation: str, is_async: bool) -> sdk.GroundlineError:
    with pytest.raises(sdk.GroundlineError) as raised:
        if is_async:
            asyncio.run(getattr(client, f"{operation}_async")())
        else:
            getattr(client, operation)()
    return raised.value


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    "status", [302, 400, 401, 404, 413, 429, 500, 502, 503, 504], ids=str
)
def test_an_error_for_a_status_shows_neither_the_key_nor_the_request(
    status: int, is_async: bool, fake_server: FakeServer
):
    fake_server.reply("GET", "/health", status, {"detail": "x"})
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    assert_safe(trigger(client, "health", is_async))


@SYNC_AND_ASYNC
def test_a_422_does_not_repeat_the_text_it_rejected_in_a_message(
    is_async: bool, fake_server: FakeServer
):
    # The server's 422 leaves out the rejected value; a proxy's reply might not,
    # so the message holds none of the body, and `detail` only the documented
    # location and message.
    body = {
        "detail": [{"loc": ["body"], "msg": "bad", "type": "x", "input": SOURCE_TEXT}]
    }
    fake_server.reply("GET", "/health", 422, body)
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    error = trigger(client, "health", is_async)

    assert SOURCE_TEXT not in str(error)
    assert SOURCE_TEXT not in repr(error)
    assert SOURCE_TEXT not in repr(vars(error))
    assert_safe(error)


@SYNC_AND_ASYNC
def test_an_error_for_a_refused_connection_shows_neither_the_key_nor_the_request(
    is_async: bool, closed_port: int
):
    client = sdk.GroundlineClient(
        base_url=f"http://127.0.0.1:{closed_port}", api_key=SECRET_KEY, max_attempts=1
    )

    assert_safe(trigger(client, "health", is_async))


@SYNC_AND_ASYNC
def test_an_error_for_a_timeout_shows_neither_the_key_nor_the_request(
    is_async: bool, fake_server: FakeServer
):
    fake_server.hang("GET", "/health")
    client = sdk.GroundlineClient(
        base_url=fake_server.url,
        api_key=SECRET_KEY,
        max_attempts=1,
        timeout_seconds=0.05,
    )

    assert_safe(trigger(client, "health", is_async))


@SYNC_AND_ASYNC
def test_an_error_for_an_unreadable_success_shows_neither_the_key_nor_the_request(
    is_async: bool, fake_server: FakeServer
):
    fake_server.reply("GET", "/health", 200, b"<html>not json</html>")
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    assert_safe(trigger(client, "health", is_async))


@SYNC_AND_ASYNC
def test_a_key_in_the_servers_detail_is_redacted_in_the_error(
    is_async: bool, fake_server: FakeServer
):
    echoed = "sk-ant-api03-" + "Zz9Yy8Xx" * 6
    fake_server.reply("GET", "/health", 502, {"detail": f"upstream said {echoed}"})
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    error = trigger(client, "health", is_async)

    assert error.detail == "upstream said <redacted key>"
    assert echoed not in repr(vars(error))


@SYNC_AND_ASYNC
@pytest.mark.parametrize(
    ("echo", "shown"),
    [
        (SECRET_KEY, "upstream echoed <redacted key>"),
        (f"Bearer {SECRET_KEY}", "upstream echoed Bearer <redacted key>"),
    ],
    ids=["key", "header"],
)
def test_the_clients_own_key_echoed_in_the_servers_detail_is_redacted(
    echo: str, shown: str, is_async: bool, fake_server: FakeServer
):
    # The key isn't one of the shapes `redact_keys` knows, so only the client's
    # own knowledge of it can hide it.
    fake_server.reply("GET", "/health", 502, {"detail": f"upstream echoed {echo}"})
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    error = trigger(client, "health", is_async)

    assert error.detail == shown
    assert_safe(error)


@SYNC_AND_ASYNC
def test_a_key_that_contains_a_space_is_hidden_whole_when_echoed(
    is_async: bool, fake_server: FakeServer
):
    # The client's checks allow a printable key with a space in it.
    spaced = "a key with spaces 123"
    fake_server.reply("GET", "/health", 502, {"detail": f"upstream echoed {spaced}"})
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=spaced, max_attempts=1
    )

    error = trigger(client, "health", is_async)

    assert error.detail == "upstream echoed <redacted key>"


@SYNC_AND_ASYNC
def test_a_key_in_a_proxys_error_page_never_reaches_the_error(
    is_async: bool, fake_server: FakeServer
):
    echoed = "gl_" + "A1b2C3d4" * 6
    fake_server.reply("GET", "/health", 502, f"<p>{echoed}</p>".encode())
    client = sdk.GroundlineClient(
        base_url=fake_server.url, api_key=SECRET_KEY, max_attempts=1
    )

    error = trigger(client, "health", is_async)

    assert echoed not in everything_visible_about(error)


def test_a_setup_error_does_not_chain_or_hold_the_key():
    with pytest.raises(sdk.GroundlineSetupError) as raised:
        sdk.GroundlineClient(base_url="not-a-url", api_key=SECRET_KEY)

    assert SECRET_KEY not in everything_visible_about(raised.value)
