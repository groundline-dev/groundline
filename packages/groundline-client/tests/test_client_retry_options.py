"""The options that set the client's attempts, retry time and time limits."""

from typing import Any

import groundline_client as sdk
import groundline_client.client as client_module
import pytest
from conftest import FakeServer

COUNT_OPTIONS = ["max_attempts"]
SECONDS_OPTIONS = ["max_retry_seconds", "connect_timeout_seconds", "timeout_seconds"]


def make_client(fake_server: FakeServer, api_key: str, **options: Any) -> Any:
    return sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key, **options)


def test_the_defaults_fit_a_check_that_the_server_allows_120_seconds():
    # R5 (request time limits): the server's check limit is 120 s, so one
    # attempt may take longer than that, and the retry time covers an attempt
    # plus a short retry.
    assert client_module.DEFAULT_MAX_ATTEMPTS == 3
    assert client_module.DEFAULT_MAX_RETRY_SECONDS == 180.0
    assert client_module.DEFAULT_CONNECT_TIMEOUT_SECONDS == 5.0
    assert client_module.DEFAULT_TIMEOUT_SECONDS == 150.0


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
