"""The API key never appears where anyone could read it (R5, ALL-156)."""

import asyncio
import logging
import socket
import traceback
from typing import Any

import groundline_client as sdk
import pytest
from conftest import KEY_VARIABLE, FakeServer


def make_client(base_url: str, api_key: str) -> Any:
    return sdk.GroundlineClient(base_url=base_url, api_key=api_key)


def test_the_client_shows_its_url_but_not_the_key(
    fake_server: FakeServer, api_key: str
):
    client = make_client(fake_server.url, api_key)
    assert api_key not in repr(client)
    assert api_key not in str(client)
    assert fake_server.url in repr(client)


def test_the_client_still_hides_the_key_after_a_call(
    fake_server: FakeServer, api_key: str
):
    client = make_client(fake_server.url, api_key)
    client.health()
    assert api_key not in repr(client)


def test_the_generated_client_inside_hides_the_key_before_and_after_a_call(
    fake_server: FakeServer, api_key: str
):
    # Tools that dump a traceback's local variables show this object. Without
    # the override, the generated class prints the key twice: as its token and,
    # after the first call, in its headers.
    client = make_client(fake_server.url, api_key)
    assert api_key not in repr(client._client)
    client.health()
    assert api_key not in repr(client._client)


def test_a_config_error_does_not_contain_the_key(api_key: str):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        make_client("not-a-url", api_key)
    assert api_key not in str(raised.value)
    assert api_key not in repr(raised.value)


def test_a_failed_connection_keeps_the_key_out_of_the_error_and_the_traceback(
    api_key: str,
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    client = make_client(f"http://127.0.0.1:{closed_port}", api_key)

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the type is checked elsewhere
        client.health()

    text = "".join(traceback.format_exception(raised.value))
    assert api_key not in text
    assert api_key not in str(raised.value)


def test_a_failed_async_connection_keeps_the_key_out_of_the_traceback(api_key: str):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
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
    client.health()

    assert api_key not in caplog.text


def test_the_message_for_a_missing_key_names_the_variable_not_a_value():
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url="http://example.test")
    assert KEY_VARIABLE in str(raised.value)
