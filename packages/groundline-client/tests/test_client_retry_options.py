"""The options that set the client's attempts, retry time and time limits."""

import ast
import inspect
import textwrap
from typing import Any

import groundline_client as sdk
import groundline_client.client as client_module
import pytest
from conftest import FakeServer, make_client
from groundline_client._generated.client import (  # pyright: ignore[reportPrivateUsage]
    AuthenticatedClient,
)
from groundline_client._http import Limits  # pyright: ignore[reportPrivateUsage]

COUNT_OPTIONS = ["max_attempts"]
SECONDS_OPTIONS = ["max_retry_seconds", "connect_timeout_seconds", "timeout_seconds"]


@pytest.mark.parametrize(
    "options",
    [
        {"max_attempts": 1},
        {"max_attempts": 7},
        {"max_retry_seconds": 0.5},
        {"connect_timeout_seconds": 2},
        {"timeout_seconds": 600},
        # The retry time may be shorter than one attempt's limit: the first
        # attempt is then cut off at the retry time.
        {"max_retry_seconds": 10.0, "timeout_seconds": 150.0},
    ],
    ids=str,
)
def test_valid_options_are_accepted(
    options: dict[str, Any], fake_server: FakeServer, api_key: str
):
    make_client(fake_server, api_key, **options)


@pytest.mark.parametrize("name", COUNT_OPTIONS)
@pytest.mark.parametrize("value", [0, -3, 1.5, True, "3", None, float("nan")])
def test_a_bad_attempt_count_is_refused_before_any_request(
    name: str, value: Any, fake_server: FakeServer, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError) as refused:
        make_client(fake_server, api_key, **{name: value})

    assert f"{name}=" in str(refused.value)
    assert fake_server.requests == []


@pytest.mark.parametrize("name", SECONDS_OPTIONS)
@pytest.mark.parametrize(
    "value", [0, -2.5, float("nan"), float("inf"), True, "5", None]
)
def test_a_bad_number_of_seconds_is_refused_before_any_request(
    name: str, value: Any, fake_server: FakeServer, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError) as refused:
        make_client(fake_server, api_key, **{name: value})

    assert f"{name}=" in str(refused.value)
    assert fake_server.requests == []


@pytest.mark.parametrize("name", COUNT_OPTIONS + SECONDS_OPTIONS)
def test_the_error_names_the_option_but_never_the_value(
    name: str, fake_server: FakeServer, api_key: str
):
    value = "not-a-number-7391"

    with pytest.raises(sdk.GroundlineConfigError) as refused:
        make_client(fake_server, api_key, **{name: value})

    assert value not in str(refused.value)
    assert value not in repr(refused.value)


def test_every_bad_option_is_reported_together(fake_server: FakeServer, api_key: str):
    with pytest.raises(sdk.GroundlineConfigError) as refused:
        make_client(fake_server, api_key, max_attempts=0, timeout_seconds=-1)

    message = str(refused.value)
    assert "max_attempts=" in message
    assert "timeout_seconds=" in message


@pytest.mark.parametrize("name", SECONDS_OPTIONS)
def test_a_whole_number_too_big_for_a_float_is_refused_with_a_config_error(
    name: str, fake_server: FakeServer, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError) as refused:
        make_client(fake_server, api_key, **{name: 10**400})

    assert f"{name}=" in str(refused.value)


def test_the_retrying_clients_get_the_arguments_the_generated_client_builds_with():
    # `_httpx_arguments` copies the body of the generated `get_httpx_client`. If
    # `make generate-sdk` adds or renames an argument there, this fails.
    source = textwrap.dedent(inspect.getsource(AuthenticatedClient.get_httpx_client))
    construction = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Client"
    )
    generated = {keyword.arg for keyword in construction.keywords if keyword.arg}

    ours = client_module._ApiKeyHidingClient(  # pyright: ignore[reportPrivateUsage]
        base_url="http://example.invalid",
        token="not-a-real-key",  # betterleaks:allow a dummy value
        limits=Limits(1, 1.0, 1.0, 1.0),
    )._httpx_arguments()  # pyright: ignore[reportPrivateUsage]

    # `timeout` is left out on purpose: each attempt sets its own.
    assert set(ours) == generated - {"timeout"}
