"""What each client method sends and returns, sync and async."""

import ast
import importlib
import inspect
import pkgutil
import textwrap
from typing import Any, cast

import groundline_client as sdk
import pytest
from conftest import OPERATIONS, PROBE_REPLY, FakeServer, call, request_for


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


def test_every_generated_operation_has_the_two_helpers_the_client_calls():
    # `_call` builds the request and reads the answer with these, so a caller's
    # own mistake isn't reported as a bad answer. A regeneration that renames
    # them must fail here, not at a user's call.
    api = importlib.import_module("groundline_client._generated.api")
    missing: list[str] = []
    for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}."):
        if module.ispkg:
            continue
        generated = importlib.import_module(module.name)
        for helper in ("_get_kwargs", "_parse_response"):
            if not callable(getattr(generated, helper, None)):
                missing.append(f"{module.name}.{helper}")
    assert not missing, f"the client calls these generated helpers: {missing}"


def _steps(function: Any) -> list[str]:
    """The statements of a function's body, as text, without its docstring."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    body = cast(ast.FunctionDef | ast.AsyncFunctionDef, tree.body[0]).body[1:]
    return [ast.unparse(statement) for statement in body]


def test_every_generated_operation_still_takes_the_steps_the_client_copies():
    # `_call` and `_call_async` repeat the steps of the generated `sync_detailed`
    # and `asyncio_detailed`: build the request, send it, parse the answer. If a
    # regeneration changed them, such as renaming a keyword, the parsing would
    # fail on every answer and each success would look unreadable, after the
    # paid call. So this fails when a regeneration changes the steps.
    api = importlib.import_module("groundline_client._generated.api")
    changed: list[str] = []
    for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}."):
        if module.ispkg:
            continue
        generated = importlib.import_module(module.name)
        sync = _steps(generated.sync_detailed)
        asynchronous = _steps(generated.asyncio_detailed)
        built = ast.parse(
            textwrap.dedent(inspect.getsource(generated._build_response))  # pyright: ignore[reportPrivateUsage]
        )
        expected_parse = "parsed=_parse_response(client=client, response=response)"
        ok = (
            len(sync) == 3
            and sync[0].startswith("kwargs = _get_kwargs(")
            and sync[1] == "response = client.get_httpx_client().request(**kwargs)"
            and sync[2] == "return _build_response(client=client, response=response)"
            and len(asynchronous) == 3
            and asynchronous[0].startswith("kwargs = _get_kwargs(")
            and asynchronous[1]
            == "response = await client.get_async_httpx_client().request(**kwargs)"
            and asynchronous[2] == sync[2]
            and any(
                ast.unparse(keyword) == expected_parse
                for node in ast.walk(built)
                if isinstance(node, ast.Call)
                for keyword in node.keywords
            )
        )
        if not ok:
            changed.append(module.name)
    assert not changed, f"update `_call` in client.py for the regenerated: {changed}"
