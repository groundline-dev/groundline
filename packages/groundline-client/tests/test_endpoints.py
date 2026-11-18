"""Each endpoint in openapi.json has a generated SDK function and models (ALL-155).

For every operation, the generated public function (`sync_detailed`) sends the
request the spec describes (method, URL and body) and parses every reply the
spec documents, 200 and errors, into the model the spec names. The server is a
`MockTransport` that answers with the spec's own examples, so the tests use no
private generated code and go through the parser the SDK really uses.
A route added to the API without an SDK method makes the first test fail.
"""

import importlib
import json
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from groundline_client._generated.client import AuthenticatedClient

PACKAGE_DIR = Path(__file__).resolve().parents[1]
SPEC = json.loads((PACKAGE_DIR / "openapi.json").read_text())
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]

# operation ID -> (request model, 200 response model); no request model means
# the operation takes no body.
OPERATIONS: dict[str, tuple[str | None, str]] = {
    "evaluate_omissions": ("OmissionsRequest", "OmissionsResponse"),
    "evaluate_commissions": ("CommissionsRequest", "CommissionsResponse"),
    "get_health": (None, "GetHealthResponseGetHealth"),
    "get_ready": (None, "GetReadyResponseGetReady"),
}
# The probes' replies are free-form maps of text, so the spec has no example.
PROBE_REPLY = {"status": "ok"}


HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def spec_operations() -> dict[str, tuple[str, str]]:
    """Operation ID -> (HTTP method, path), as openapi.json lists them."""
    return {
        item[method]["operationId"]: (method, path)
        for path, item in SPEC["paths"].items()
        for method in HTTP_METHODS
        if method in item
    }


def json_content(operation_id: str, status: str) -> dict[str, Any] | None:
    """The JSON content of one documented reply, or None if it has no JSON body."""
    method, path = spec_operations()[operation_id]
    response = SPEC["paths"][path][method]["responses"][status]
    return response.get("content", {}).get("application/json")


def generated_functions() -> dict[str, Any]:
    """Operation ID -> the generated module that holds its function."""
    api = importlib.import_module("groundline_client._generated.api")
    return {
        module.name.rsplit(".", 1)[1]: importlib.import_module(module.name)
        for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}.")
        if not module.ispkg
    }


def model(name: str) -> Any:
    return getattr(importlib.import_module("groundline_client._generated.models"), name)


def test_the_spec_this_test_knows_and_the_generated_sdk_cover_the_same_operations():
    in_spec = set(spec_operations())
    assert in_spec == set(OPERATIONS), "add the new operation to OPERATIONS"
    assert set(generated_functions()) == in_spec, "run make generate-sdk"


def reply_examples(operation_id: str, status: str) -> list[Any]:
    """The documented examples of one reply, or the probe reply for a probe."""
    content = json_content(operation_id, status)
    assert content is not None, f"{operation_id} {status} has no JSON body"
    if "examples" in content:
        return [example["value"] for example in content["examples"].values()]
    schema_name = content["schema"].get("$ref", "").rsplit("/", 1)[-1]
    if schema_name in SCHEMAS and "examples" in SCHEMAS[schema_name]:
        return SCHEMAS[schema_name]["examples"]
    return [PROBE_REPLY]


def documented_replies(*, with_json: bool) -> list[tuple[str, str, int]]:
    """(operation ID, model the spec names, status) for every documented reply
    that has a JSON body, or for those that have none."""
    replies: list[tuple[str, str, int]] = []
    for operation_id, (method, path) in spec_operations().items():
        for status in SPEC["paths"][path][method]["responses"]:
            content = json_content(operation_id, status)
            if (content is not None) != with_json:
                continue
            schema: dict[str, Any] = content["schema"] if content else {}
            # The probes' 200 replies are free-form maps, which the generator
            # names after the operation (see OPERATIONS).
            name: str = (
                str(schema["$ref"]).rsplit("/", 1)[-1]
                if "$ref" in schema
                else OPERATIONS[operation_id][1]
            )
            replies.append((operation_id, name, int(status)))
    return replies


def replying(status: int, example: Any) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _request: httpx2.Response(status, json=example)


def call(
    operation_id: str, handler: Callable[[httpx2.Request], httpx2.Response]
) -> Any:
    """Call the generated public function against a fake server."""
    request_model, _ = OPERATIONS[operation_id]
    client = AuthenticatedClient(
        base_url="http://groundline.test",
        token="test-key",
        httpx_args={"transport": httpx2.MockTransport(handler)},
    )
    function = generated_functions()[operation_id]
    if request_model is None:
        return function.sync_detailed(client=client)
    example = SCHEMAS[request_model]["examples"][0]
    return function.sync_detailed(
        client=client, body=model(request_model).from_dict(example)
    )


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_the_generated_function_sends_the_request_the_spec_describes(
    operation_id: str,
):
    method, path = spec_operations()[operation_id]
    request_model, _ = OPERATIONS[operation_id]
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=reply_examples(operation_id, "200")[0])

    call(operation_id, handler)
    assert len(seen) == 1
    assert seen[0].method.lower() == method
    assert seen[0].url.path == path
    if request_model is not None:
        assert json.loads(seen[0].content) == SCHEMAS[request_model]["examples"][0]


@pytest.mark.parametrize(
    ("operation_id", "model_name", "status"), documented_replies(with_json=True)
)
def test_the_generated_function_parses_each_documented_reply_into_the_named_model(
    operation_id: str, model_name: str, status: int
):
    for example in reply_examples(operation_id, str(status)):
        response = call(operation_id, replying(status, example))
        assert response.status_code == status
        assert type(response.parsed) is model(model_name)
        assert response.parsed.to_dict() == example


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_the_map_names_the_model_the_generated_parser_returns_for_a_200(
    operation_id: str,
):
    _, response_model = OPERATIONS[operation_id]
    example = reply_examples(operation_id, "200")[0]
    response = call(operation_id, replying(200, example))
    assert type(response.parsed) is model(response_model)


def test_every_documented_reply_has_a_json_body():
    # The tests above parse JSON replies only. A reply with no body would be
    # skipped silently, so this fails when the API documents one.
    without_body = documented_replies(with_json=False)
    assert not without_body, (
        f"add a test that these parse to None, then update this one: {without_body}"
    )
