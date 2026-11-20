"""The API key never appears where anyone could read it."""

import asyncio
import logging
import traceback
from typing import Any

import groundline_client as sdk
import pytest
from conftest import API_KEY_VARIABLE, FakeServer

# A module-level constant, not the `api_key` fixture: a fixture value would be a
# local variable of the test and show up in its own traceback.
SECRET_KEY = "test-key-not-a-real-secret"  # betterleaks:allow a dummy value


def with_locals(error: BaseException) -> str:
    """The traceback as tools that print each frame's local variables show it."""
    return "".join(
        traceback.TracebackException.from_exception(error, capture_locals=True).format()
    )


def make_client(api_url: str, api_key: str) -> Any:
    return sdk.GroundlineClient(api_url=api_url, api_key=api_key)


def test_the_client_shows_its_url_but_not_the_api_key(
    fake_server: FakeServer, api_key: str
):
    client = make_client(fake_server.url, api_key)
    assert api_key not in repr(client)
    assert api_key not in str(client)
    assert fake_server.url in repr(client)
    assert repr(client).startswith("GroundlineClient(api_url=")


def test_the_client_still_hides_the_api_key_after_a_call(
    fake_server: FakeServer, api_key: str
):
    client = make_client(fake_server.url, api_key)
    client.health()
    assert api_key not in repr(client)


def test_the_generated_client_inside_hides_the_api_key_before_and_after_a_call(
    fake_server: FakeServer, api_key: str
):
    # Tools that dump a traceback's local variables show this object. Without
    # the override, the generated class prints the key twice: as its token and,
    # after the first call, in its headers.
    client = make_client(fake_server.url, api_key)
    assert api_key not in repr(client._client)
    client.health()
    assert api_key not in repr(client._client)


def test_a_setup_error_does_not_contain_the_api_key(api_key: str):
    with pytest.raises(sdk.GroundlineSetupError) as raised:
        make_client("not-a-url", api_key)
    assert api_key not in str(raised.value)
    assert api_key not in repr(raised.value)


def test_a_failed_connection_keeps_the_api_key_out_of_the_error_and_the_traceback(
    api_key: str, closed_port: int
):
    client = make_client(f"http://127.0.0.1:{closed_port}", api_key)

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is checked elsewhere
        client.health()

    text = "".join(traceback.format_exception(raised.value))
    assert api_key not in text
    assert api_key not in str(raised.value)


def test_a_failed_async_connection_keeps_the_api_key_out_of_the_traceback(
    api_key: str, closed_port: int
):
    client = make_client(f"http://127.0.0.1:{closed_port}", api_key)

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is checked elsewhere
        asyncio.run(client.health_async())

    assert api_key not in "".join(traceback.format_exception(raised.value))


def test_logs_at_debug_level_never_contain_the_key(
    fake_server: FakeServer, api_key: str, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    client = make_client(fake_server.url, api_key)
    client.health()
    asyncio.run(client.ready_async())
    fake_server.reply("GET", "/health", 500, {"detail": "boom"})
    with pytest.raises(sdk.ServerUnavailableError):
        client.health()

    assert api_key not in caplog.text


def test_the_message_for_a_missing_key_names_the_variable_not_a_value():
    with pytest.raises(sdk.GroundlineSetupError) as raised:
        sdk.GroundlineClient(api_url="http://example.test")
    assert API_KEY_VARIABLE in str(raised.value)


def test_a_setup_error_traceback_does_not_hold_the_api_key_in_local_variables():
    with pytest.raises(sdk.GroundlineSetupError) as raised:
        sdk.GroundlineClient(api_key=SECRET_KEY)  # no URL, so creation fails
    assert SECRET_KEY not in with_locals(raised.value)


def test_a_failed_request_traceback_does_not_hold_the_api_key_in_local_variables(
    closed_port: int,
):
    client = make_client(f"http://127.0.0.1:{closed_port}", SECRET_KEY)
    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is checked elsewhere
        client.health()
    assert SECRET_KEY not in with_locals(raised.value)


def test_a_failed_async_request_traceback_does_not_hold_the_api_key_in_local_variables(
    closed_port: int,
):
    client = make_client(f"http://127.0.0.1:{closed_port}", SECRET_KEY)
    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is checked elsewhere
        asyncio.run(client.health_async())
    assert SECRET_KEY not in with_locals(raised.value)
