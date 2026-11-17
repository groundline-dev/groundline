"""Where the client's URL and API key come from, and what if they're missing.

Covers R1, R2 and R3 of ALL-156.
"""

from typing import Any

import groundline_client as sdk
import pytest
from conftest import API_KEY_VARIABLE, URL_VARIABLE, FakeServer

PUBLIC_NAMES = [
    "GroundlineClient",
    "GroundlineConfigError",
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


def test_an_empty_argument_falls_back_to_the_environment_variable(
    fake_server: FakeServer, api_key: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(URL_VARIABLE, fake_server.url)
    monkeypatch.setenv(API_KEY_VARIABLE, api_key)
    client: Any = sdk.GroundlineClient(base_url="", api_key="")
    client.health()
    assert fake_server.requests[-1].headers["authorization"] == f"Bearer {api_key}"


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
def test_a_blank_value_counts_as_missing(
    blank: str, fake_server: FakeServer, api_key: str
):
    with pytest.raises(sdk.GroundlineConfigError, match=API_KEY_VARIABLE):
        sdk.GroundlineClient(base_url=fake_server.url, api_key=blank)
    with pytest.raises(sdk.GroundlineConfigError, match=URL_VARIABLE):
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
