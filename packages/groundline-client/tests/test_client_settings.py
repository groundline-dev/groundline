"""Where the client's URL and API key come from, and what if they're missing."""

from typing import Any

import groundline_client as sdk
import pytest
from conftest import API_KEY_VARIABLE, URL_VARIABLE, FakeServer

PUBLIC_NAMES = [
    "GroundlineClient",
    "GroundlineConfigError",
    "GroundlineError",
    "AuthenticationError",
    "RateLimitError",
    "InvalidRequestError",
    "RequestTooLargeError",
    "ServerUnavailableError",
    "GroundlineTimeoutError",
    "OmissionsRequest",
    "OmissionsResponse",
    "CommissionsRequest",
    "CommissionsResponse",
    "ErrorBody",
    "InvalidRequestBody",
    "GetHealthResponseGetHealth",
    "GetReadyResponseGetReady",
]
UNUSED_URL = "http://127.0.0.1:9"  # nothing listens here; a request would fail


def test_the_client_and_the_models_its_methods_use_are_importable_from_the_package():
    missing = [name for name in PUBLIC_NAMES if not hasattr(sdk, name)]
    assert not missing
    assert set(PUBLIC_NAMES) <= set(sdk.__all__)


def test_a_config_error_is_a_value_error():
    assert issubclass(sdk.GroundlineConfigError, ValueError)


def test_arguments_alone_are_enough(fake_server: FakeServer, api_key: str):
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)
    client.health()
    assert fake_server.requests[-1].headers["authorization"] == f"Bearer {api_key}"


def test_environment_variables_alone_are_enough(
    fake_server: FakeServer, api_key: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(URL_VARIABLE, fake_server.url)
    monkeypatch.setenv(API_KEY_VARIABLE, api_key)
    client: Any = sdk.GroundlineClient()
    client.health()
    assert fake_server.requests[-1].headers["authorization"] == f"Bearer {api_key}"


def test_arguments_win_over_environment_variables(
    fake_server: FakeServer, api_key: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(URL_VARIABLE, UNUSED_URL)
    monkeypatch.setenv(API_KEY_VARIABLE, "environment-key")
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)
    client.health()
    assert fake_server.requests[-1].headers["authorization"] == f"Bearer {api_key}"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_argument_raises_instead_of_using_the_environment_variable(
    blank: str, fake_server: FakeServer, api_key: str, monkeypatch: pytest.MonkeyPatch
):
    # A blank argument usually means a secret that didn't load. Using the
    # variable would send a different API key from the one the caller meant.
    monkeypatch.setenv(URL_VARIABLE, fake_server.url)
    monkeypatch.setenv(API_KEY_VARIABLE, api_key)
    with pytest.raises(sdk.GroundlineConfigError, match=r"api_key= is blank"):
        sdk.GroundlineClient(api_key=blank)
    with pytest.raises(sdk.GroundlineConfigError, match=r"base_url= is blank"):
        sdk.GroundlineClient(base_url=blank)


def test_a_trailing_slash_on_the_url_is_tolerated(
    fake_server: FakeServer, api_key: str
):
    client: Any = sdk.GroundlineClient(base_url=fake_server.url + "/", api_key=api_key)
    client.health()
    assert fake_server.requests[-1].path == "/health"


def test_a_missing_url_fails_at_once_and_names_only_the_url(api_key: str):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(api_key=api_key)
    assert URL_VARIABLE in str(raised.value)
    assert API_KEY_VARIABLE not in str(raised.value)


def test_a_missing_api_key_fails_at_once_and_names_only_the_api_key(
    fake_server: FakeServer,
):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url=fake_server.url)
    assert API_KEY_VARIABLE in str(raised.value)
    assert URL_VARIABLE not in str(raised.value)


def test_missing_both_names_both():
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient()
    assert URL_VARIABLE in str(raised.value)
    assert API_KEY_VARIABLE in str(raised.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_argument_fails_at_once_even_with_no_variable_set(
    blank: str, fake_server: FakeServer, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError, match="api_key= is blank"):
        sdk.GroundlineClient(base_url=fake_server.url, api_key=blank)
    with pytest.raises(sdk.GroundlineConfigError, match="base_url= is blank"):
        sdk.GroundlineClient(base_url=blank, api_key=api_key)


def test_a_blank_environment_variable_counts_as_missing(
    api_key: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(URL_VARIABLE, "  ")
    with pytest.raises(sdk.GroundlineConfigError, match=URL_VARIABLE):
        sdk.GroundlineClient(api_key=api_key)


@pytest.mark.parametrize(
    "bad_url", ["localhost:8000", "ftp://example.test", "SENTINEL-not-a-url"]
)
def test_a_url_that_is_not_http_or_https_fails_without_echoing_it(
    bad_url: str, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url=bad_url, api_key=api_key)
    assert URL_VARIABLE in str(raised.value)
    assert bad_url not in str(raised.value)


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://host:SENTINEL",  # a port that isn't a number
        "http://:80/SENTINEL",  # no host
        "http://SENTINEL host",  # a space in the host
        "http://user:SENTINEL@host",  # a password in the URL; betterleaks:allow a dummy
        "http://SENTINEL@host",  # a user name in the URL
        "https://host/?x=SENTINEL",  # a query string
        "https://host/#SENTINEL",  # a fragment
    ],
)
def test_a_url_that_a_request_would_reject_fails_at_creation_without_echoing_it(
    bad_url: str, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url=bad_url, api_key=api_key)
    assert URL_VARIABLE in str(raised.value)
    assert "SENTINEL" not in str(raised.value)
    assert "SENTINEL" not in repr(raised.value)


def test_a_malformed_url_raises_a_config_error_without_echoing_it(api_key: str):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url="http://[SENTINEL", api_key=api_key)
    assert URL_VARIABLE in str(raised.value)
    assert "SENTINEL" not in str(raised.value)


@pytest.mark.parametrize("bad_key", ["kéy-SECRET", "ke\ny-SECRET"])
def test_an_api_key_that_cannot_go_in_a_header_fails_at_creation_without_echoing_it(
    bad_key: str, fake_server: FakeServer
):
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url=fake_server.url, api_key=bad_key)
    assert API_KEY_VARIABLE in str(raised.value)
    assert "SECRET" not in str(raised.value)
    assert "SECRET" not in repr(raised.value)


def test_an_environment_api_key_that_cannot_go_in_a_header_also_fails_at_creation(
    fake_server: FakeServer, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(API_KEY_VARIABLE, "kéy-SECRET")
    with pytest.raises(sdk.GroundlineConfigError) as raised:
        sdk.GroundlineClient(base_url=fake_server.url)
    assert "SECRET" not in repr(raised.value)
