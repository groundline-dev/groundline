"""What each client method sends and returns, sync and async (R1, R4, ALL-156)."""

import asyncio
import importlib
import pkgutil
from typing import Any

import groundline_client as sdk
import httpx2
import pytest
from conftest import PROBE_REPLY, FakeServer

# method name, HTTP method, path, request model, response model
OPERATIONS = [
    (
        "evaluate_omissions",
        "POST",
        "/v1/omissions",
        "OmissionsRequest",
        "OmissionsResponse",
    ),
    (
        "evaluate_commissions",
        "POST",
        "/v1/commissions",
        "CommissionsRequest",
        "CommissionsResponse",
    ),
    ("health", "GET", "/health", None, None),
    ("ready", "GET", "/ready", None, None),
]


def call(client: Any, operation: str, request: Any, *, is_async: bool) -> Any:
    arguments = [] if request is None else [request]
    if is_async:
        return asyncio.run(getattr(client, f"{operation}_async")(*arguments))
    return getattr(client, operation)(*arguments)


def request_for(fake_server: FakeServer, request_model: str | None) -> Any:
    if request_model is None:
        return None
    return getattr(sdk, request_model).from_dict(fake_server.examples[request_model])


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("operation", "http_method", "path", "request_model", "response_model"), OPERATIONS
)
def test_each_method_calls_its_route_and_returns_the_parsed_reply(
    operation: str,
    http_method: str,
    path: str,
    request_model: str | None,
    response_model: str | None,
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
):
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)
    request = request_for(fake_server, request_model)

    result = call(client, operation, request, is_async=is_async)

    sent = fake_server.requests[-1]
    assert (sent.method, sent.path) == (http_method, path)
    if request_model is not None:
        assert sent.body == fake_server.examples[request_model]
    if response_model is None:
        assert result.to_dict() == PROBE_REPLY
    else:
        assert isinstance(result, getattr(sdk, response_model))
        assert result.to_dict() == fake_server.examples[response_model]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    ("operation", "request_model"), [(o[0], o[3]) for o in OPERATIONS]
)
def test_every_call_sends_the_api_key_as_a_bearer_token(
    operation: str,
    request_model: str | None,
    is_async: bool,
    fake_server: FakeServer,
    api_key: str,
):
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)

    call(client, operation, request_for(fake_server, request_model), is_async=is_async)

    assert fake_server.requests[-1].headers["authorization"] == f"Bearer {api_key}"


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_an_error_answer_comes_back_as_a_parsed_error_body(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.reply("POST", "/v1/omissions", 401, {"detail": "Invalid API key."})
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)

    result = call(
        client,
        "evaluate_omissions",
        request_for(fake_server, "OmissionsRequest"),
        is_async=is_async,
    )

    assert isinstance(result, sdk.ErrorBody)
    assert result.detail == "Invalid API key."


def test_an_undocumented_status_comes_back_as_none(
    fake_server: FakeServer, api_key: str
):
    fake_server.reply("GET", "/health", 418, {"detail": "teapot"})
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)

    assert client.health() is None


def test_a_server_that_is_not_listening_raises_a_connection_error(
    api_key: str, closed_port: int
):
    client: Any = sdk.GroundlineClient(
        base_url=f"http://127.0.0.1:{closed_port}", api_key=api_key
    )

    with pytest.raises(httpx2.ConnectError):
        client.health()


# The generated operations whose client method has a different name.
CLIENT_METHOD_NAMES = {"get_health": "health", "get_ready": "ready"}


def test_every_generated_operation_has_a_client_method_and_an_async_twin():
    api = importlib.import_module("groundline_client._generated.api")
    operations = {
        module.name.rsplit(".", 1)[1]
        for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}.")
        if not module.ispkg
    }
    expected = {
        name
        for operation in operations
        for name in (
            CLIENT_METHOD_NAMES.get(operation, operation),
            f"{CLIENT_METHOD_NAMES.get(operation, operation)}_async",
        )
    }
    missing = sorted(
        name for name in expected if not hasattr(sdk.GroundlineClient, name)
    )
    assert not missing, f"add these methods to GroundlineClient: {missing}"
