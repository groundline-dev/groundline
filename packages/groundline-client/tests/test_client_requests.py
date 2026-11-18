"""What each client method sends and returns, sync and async."""

import asyncio
import importlib
import inspect
import pkgutil
from typing import Any

import groundline_client as sdk
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
def test_an_error_answer_raises_instead_of_returning_the_error_body(
    is_async: bool, fake_server: FakeServer, api_key: str
):
    fake_server.reply("POST", "/v1/omissions", 401, {"detail": "Invalid API key."})
    client: Any = sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)

    with pytest.raises(sdk.AuthenticationError) as raised:
        call(
            client,
            "evaluate_omissions",
            request_for(fake_server, "OmissionsRequest"),
            is_async=is_async,
        )

    assert raised.value.detail == "Invalid API key."


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


def test_each_client_method_takes_the_same_parameters_as_the_generated_function():
    # A parameter the API gains later must not be silently left out of the client.
    # Kinds are not compared: the generated functions take keywords only.
    api = importlib.import_module("groundline_client._generated.api")
    mismatched: list[str] = []
    for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}."):
        if module.ispkg:
            continue
        operation = module.name.rsplit(".", 1)[1]
        generated = importlib.import_module(module.name)
        method_name = CLIENT_METHOD_NAMES.get(operation, operation)
        for suffix, function in (("", generated.sync), ("_async", generated.asyncio)):
            expected = [
                (p.name, p.default, p.annotation)
                for p in inspect.signature(function).parameters.values()
                if p.name != "client"
            ]
            method = getattr(sdk.GroundlineClient, method_name + suffix)
            actual = [
                (p.name, p.default, p.annotation)
                for p in inspect.signature(method).parameters.values()
                if p.name != "self"
            ]
            if actual != expected:
                mismatched.append(method_name + suffix)
    assert not mismatched, f"these methods differ from the generated ones: {mismatched}"
