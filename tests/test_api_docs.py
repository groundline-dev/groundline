"""The API's docs: the /api page, the version label, and the examples."""

from importlib.metadata import version
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from groundline_core._disclaimer import SHORT_DISCLAIMER
from groundline_core._evaluate import DEFAULT_COMMISSIONS_PRESET, DEFAULT_PRESET
from groundline_core.api.commissions import CommissionsRequest, CommissionsResponse
from groundline_core.api.common import ApiModel, ErrorBody, InvalidRequestBody
from groundline_core.api.errors import DATABASE_UNAVAILABLE
from groundline_core.api.omissions import OmissionsRequest, OmissionsResponse
from groundline_core.app import app
from groundline_core.presets import load_preset
from tests.auth_helpers import ENDPOINTS, HEADERS, FakeKeys
from tests.disclaimer_helpers import clinical_disclaimer, one_line

http = TestClient(app)  # no API key: the docs are public
schema = app.openapi()
CHECKS = [endpoint.path for endpoint in ENDPOINTS]
ERROR_MODELS: dict[str, type[ApiModel]] = {
    "ErrorBody": ErrorBody,
    "InvalidRequestBody": InvalidRequestBody,
}


def test_api_serves_the_interactive_docs_without_a_key():
    response = http.get("/api")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "swagger-ui" in response.text
    assert f"'{app.openapi_url}'" in response.text


def test_the_docs_show_groundlines_release_and_label_the_api_version():
    assert schema["info"]["version"] == version("groundline-core")
    assert "`/v1`" in schema["info"]["description"]
    assert "`/v2`" in schema["info"]["description"]
    assert [tag["name"] for tag in schema["tags"]] == ["v1"]
    for path in CHECKS:
        assert schema["paths"][path]["post"]["tags"] == ["v1"]


def test_the_docs_intro_carries_the_short_disclaimer_and_asks_to_show_the_field():
    description = one_line(schema["info"]["description"])

    assert SHORT_DISCLAIMER in description
    assert "`metadata.disclaimer`" in description


@pytest.mark.parametrize("model", ["OmissionsResponse", "CommissionsResponse"])
def test_each_checks_example_shows_the_clinical_disclaimer(model: str):
    metadata = schema["components"]["schemas"][model]["examples"][0]["metadata"]

    assert metadata.get("disclaimer") == clinical_disclaimer(), metadata


def test_every_documented_route_has_the_global_body_limit_response():
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            response = operation["responses"]["413"]
            assert "every HTTP path and method" in response["description"], (
                path,
                method,
                response,
            )
            example = response["content"]["application/json"]["examples"][
                "body_too_large"
            ]
            ErrorBody.model_validate(example["value"])


def _resolve(ref: dict[str, Any]) -> dict[str, Any]:
    name = ref["$ref"].removeprefix("#/components/schemas/")
    return schema["components"]["schemas"][name]


@pytest.mark.parametrize(
    ("path", "request_model", "response_model"),
    [
        ("/v1/omissions", OmissionsRequest, OmissionsResponse),
        ("/v1/commissions", CommissionsRequest, CommissionsResponse),
    ],
)
def test_each_check_has_a_valid_example_request_and_response(
    path: str, request_model: type[ApiModel], response_model: type[ApiModel]
):
    operation = schema["paths"][path]["post"]
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]

    for model, ref in [(request_model, request), (response_model, response)]:
        examples = _resolve(ref)["examples"]
        assert examples
        for example in examples:
            model.model_validate(example)


def test_the_served_examples_keep_the_nulls_a_real_response_has():
    # FastAPI strips every null from the schema it builds, so the docs would show
    # a commissions response without `counts` and `summary_evidence`.
    served = http.get("/openapi.json").json()
    example = served["components"]["schemas"]["CommissionsResponse"]["examples"][0]

    assert "counts" in example and example["counts"] is None
    finding = example["findings"][0]
    assert "summary_evidence" in finding and finding["summary_evidence"] is None


@pytest.mark.parametrize(
    ("model", "empty_case"),
    [("OmissionsResponse", "no facts"), ("CommissionsResponse", "no claims")],
)
def test_each_score_is_defined_in_the_docs(model: str, empty_case: str):
    text = schema["components"]["schemas"][model]["properties"]["score"]["description"]

    for phrase in ("From 0 to 1", "Higher is better", "below 0.5", "alarm"):
        assert phrase in text
    assert empty_case in text


@pytest.mark.parametrize("path", CHECKS)
def test_each_check_has_a_valid_example_for_every_error(path: str):
    responses = schema["paths"][path]["post"]["responses"]

    assert set(responses) == {
        "200",
        "400",
        "401",
        "413",
        "422",
        "500",
        "502",
        "503",
        "504",
    }
    for status, response in responses.items():
        if status == "200":
            continue
        content = response["content"]["application/json"]
        model = ERROR_MODELS[content["schema"]["$ref"].rsplit("/", 1)[-1]]
        examples = content["examples"]
        assert examples, status
        for example in examples.values():
            assert example["summary"], status
            model.model_validate(example["value"])


def test_a_real_422_has_the_documented_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)

    response = http.post(CHECKS[0], json={"source": ""}, headers=HEADERS)

    assert response.status_code == 422
    InvalidRequestBody.model_validate(response.json())


def test_api_and_docs_are_the_same_page_behind_a_proxy():
    behind_proxy = TestClient(app, root_path="/groundline")

    api = behind_proxy.get("/api").text
    docs = behind_proxy.get("/docs").text

    assert "'/groundline/openapi.json'" in api
    assert api == docs


def test_a_401_documents_its_bearer_challenge():
    responses = schema["paths"][CHECKS[0]]["post"]["responses"]

    assert "WWW-Authenticate" in responses["401"]["headers"]


@pytest.mark.parametrize(
    ("path", "preset"),
    [
        ("/v1/omissions", DEFAULT_PRESET),
        ("/v1/commissions", DEFAULT_COMMISSIONS_PRESET),
    ],
)
def test_each_checks_502_examples_name_its_own_first_stage(path: str, preset: str):
    spec = load_preset(preset)
    stage = next(s.step for s in spec.stages if s.enabled and "model" in s.settings)
    response = schema["paths"][path]["post"]["responses"]["502"]
    examples = list(response["content"]["application/json"]["examples"].values())

    assert len(examples) == 2
    assert all(f"stage '{stage}'" in e["value"]["detail"] for e in examples)


@pytest.mark.parametrize("path", CHECKS)
def test_the_503_example_names_the_stages_needing_the_anthropic_key(path: str):
    response = schema["paths"][path]["post"]["responses"]["503"]
    examples = response["content"]["application/json"]["examples"]
    detail = examples["anthropic_key_missing"]["value"]["detail"]

    assert "  Needed by: " in detail and "(claude-" in detail
    assert "(no price" not in str(schema["paths"][path]["post"]["responses"]["502"])


def test_the_commissions_docs_offer_both_presets_with_quick_as_the_default():
    operation = schema["paths"]["/v1/commissions"]["post"]
    body = _resolve(operation["requestBody"]["content"]["application/json"]["schema"])
    preset = body["properties"]["preset"]

    assert preset["enum"] == ["commission_quick", "commission_reverse_lean"]
    assert preset["default"] == DEFAULT_COMMISSIONS_PRESET
    assert "preset" not in body.get("required", [])
    # Where a caller chooses, the docs say which presets passed their
    # pre-registered tests: quick did, lean didn't (ADR 0017).
    assert "It passed its pre-registered test." in preset["description"]
    assert "isn't confirmed to that standard" in preset["description"]
    for name in preset["enum"]:
        assert name in operation["description"]
    assert "an unknown `preset`" in operation["responses"]["422"]["description"]


@pytest.mark.parametrize("route", ["/v1/omissions", "/v1/commissions"])
def test_check_documents_gateway_timeout_with_a_safe_example(route: str):
    from groundline_core.app import app

    responses = app.openapi()["paths"][route]["post"]["responses"]
    assert "504" in responses
    examples = responses["504"]["content"]["application/json"]["examples"]
    assert examples
    for example in examples.values():
        assert isinstance(example["value"]["detail"], str)


OPERATION_IDS = {
    ("post", "/v1/omissions"): "evaluate_omissions",
    ("post", "/v1/commissions"): "evaluate_commissions",
    ("get", "/health"): "get_health",
    ("get", "/ready"): "get_ready",
}
"""Each operation ID becomes a method name in the generated SDK, so changing
one is a breaking change for its users."""


def _operations() -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method, path): operation
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
    }


def test_every_route_has_an_explicit_operation_id():
    """FastAPI invents an ID from the URL when a route sets none, such as
    omissions_v1_omissions_post. Each ID becomes an SDK method name, so an
    invented one would change whenever the URL or the function name did."""
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
    ]

    assert routes
    for route in routes:
        assert route.operation_id, f"{route.path} has no operation_id"


def test_operation_ids_are_the_documented_names_and_unique():
    ids = {key: operation["operationId"] for key, operation in _operations().items()}

    assert ids == OPERATION_IDS
    assert len(set(ids.values())) == len(ids)


def test_ready_documents_its_503_with_the_error_body_and_an_example():
    response = schema["paths"]["/ready"]["get"]["responses"]["503"]
    content = response["content"]["application/json"]
    example = next(iter(content["examples"].values()))

    assert response["description"]
    assert _resolve(content["schema"])["title"] == "ErrorBody"
    assert example["value"] == {"detail": DATABASE_UNAVAILABLE}


def test_the_openapi_file_is_valid():
    validate(schema)
