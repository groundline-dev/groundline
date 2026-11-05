"""The API's docs: the /api page, the version label, and the examples."""

from importlib.metadata import version
from typing import Any

import pytest
from fastapi.testclient import TestClient

from groundline.api.commissions import CommissionsRequest, CommissionsResponse
from groundline.api.common import PROTECTED_RESPONSES, ApiModel, InvalidRequestBody
from groundline.api.omissions import OmissionsRequest, OmissionsResponse
from groundline.app import app
from tests.auth_helpers import ENDPOINTS, HEADERS, FakeKeys

http = TestClient(app)  # no API key: the docs are public
schema = app.openapi()
CHECKS = [endpoint.path for endpoint in ENDPOINTS]


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
    # Read from the docs, not the models: FastAPI drops nulls from examples.
    operation = schema["paths"][path]["post"]
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]

    for model, ref in [(request_model, request), (response_model, response)]:
        examples = _resolve(ref)["examples"]
        assert examples
        for example in examples:
            model.model_validate(example)


@pytest.mark.parametrize("path", CHECKS)
def test_each_check_has_a_valid_example_for_every_error(path: str):
    responses = schema["paths"][path]["post"]["responses"]

    assert set(responses) == {"200", "400", "401", "422", "500", "502", "503"}
    for status, response in responses.items():
        if status == "200":
            continue
        model = PROTECTED_RESPONSES[int(status)]["model"]
        examples = response["content"]["application/json"]["examples"]
        assert examples, status
        for example in examples.values():
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
