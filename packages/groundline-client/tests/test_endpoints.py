"""Each endpoint in openapi.json has a generated SDK function and models (ALL-155).

For every operation: its generated function builds the request (method, URL and
body), and the generated models build its response from the spec's own example.
A route added to the API without an SDK method makes the first test fail.
"""

import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any, cast

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SPEC = json.loads((PACKAGE / "openapi.json").read_text())
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]

# operation ID -> (request model, response model); no request model means the
# operation takes no body.
OPERATIONS: dict[str, tuple[str | None, str]] = {
    "evaluate_omissions": ("OmissionsRequest", "OmissionsResponse"),
    "evaluate_commissions": ("CommissionsRequest", "CommissionsResponse"),
    "get_health": (None, "GetHealthResponseGetHealth"),
    "get_ready": (None, "GetReadyResponseGetReady"),
}
# The probes' replies are free-form maps of text, so the spec has no example.
PROBE_REPLY = {"status": "ok"}


def spec_operations() -> dict[str, tuple[str, str]]:
    """Operation ID -> (HTTP method, path), as openapi.json lists them."""
    return {
        operation["operationId"]: (method, path)
        for path, methods in SPEC["paths"].items()
        for method, operation in methods.items()
    }


def generated_functions() -> set[str]:
    """The names of the modules that hold one generated function per operation."""
    api = importlib.import_module("groundline_client._generated.api")
    return {
        module.name.rsplit(".", 1)[1]
        for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}.")
        if not module.ispkg
    }


def function_module(operation_id: str) -> Any:
    api = importlib.import_module("groundline_client._generated.api")
    for module in pkgutil.walk_packages(api.__path__, f"{api.__name__}."):
        if module.name.rsplit(".", 1)[1] == operation_id:
            return importlib.import_module(module.name)
    raise AssertionError(f"no generated function for {operation_id}")


def model(name: str) -> Any:
    return getattr(importlib.import_module("groundline_client._generated.models"), name)


def test_the_spec_this_test_knows_and_the_generated_sdk_cover_the_same_operations():
    in_spec = set(spec_operations())
    assert in_spec == set(OPERATIONS), "add the new operation to OPERATIONS"
    assert generated_functions() == in_spec, "run make generate-sdk"


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_the_generated_function_builds_the_request(operation_id: str):
    method, path = spec_operations()[operation_id]
    request_model, _ = OPERATIONS[operation_id]
    function = function_module(operation_id)
    if request_model is None:
        kwargs = cast(dict[str, Any], function._get_kwargs())
    else:
        example = SCHEMAS[request_model]["examples"][0]
        body = model(request_model).from_dict(example)
        kwargs = cast(dict[str, Any], function._get_kwargs(body=body))
        assert kwargs["json"] == example
    assert kwargs["method"] == method
    assert kwargs["url"] == path


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_the_generated_models_build_the_response(operation_id: str):
    _, response_model = OPERATIONS[operation_id]
    examples = (
        SCHEMAS[response_model]["examples"]
        if response_model in SCHEMAS
        else [PROBE_REPLY]
    )
    for example in examples:
        response = model(response_model).from_dict(example)
        assert response.to_dict() == example
