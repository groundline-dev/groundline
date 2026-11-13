"""The API's docs: the /api page, the version label, and the examples."""

from importlib.metadata import version
from typing import Any

import pytest
from fastapi.testclient import TestClient

from groundline.api.commissions import CommissionsRequest, CommissionsResponse
from groundline.api.common import ApiModel, ErrorBody, InvalidRequestBody
from groundline.api.omissions import OmissionsRequest, OmissionsResponse
from groundline.app import app
from groundline.evaluate import DEFAULT_COMMISSIONS_PRESET, DEFAULT_PRESET
from groundline.presets import load_preset
from tests.auth_helpers import ENDPOINTS, HEADERS, FakeKeys

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
    assert schema["info"]["version"] == version("groundline")
    assert "`/v1`" in schema["info"]["description"]
    assert "`/v2`" in schema["info"]["description"]
    assert [tag["name"] for tag in schema["tags"]] == ["v1"]
    for path in CHECKS:
        assert schema["paths"][path]["post"]["tags"] == ["v1"]


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

    assert set(responses) == {"200", "400", "401", "422", "500", "502", "503"}
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
