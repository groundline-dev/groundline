"""Error responses every route shares: structured JSON, and nothing from the request."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from groundline.api import errors
from groundline.api import omissions as api
from groundline.api.errors import safe_traceback
from groundline.app import app
from tests.auth_helpers import SECRET as API_KEY
from tests.auth_helpers import FakeKeys

SECRET = "sk-ant-not-a-real-key"
http = TestClient(
    app,
    raise_server_exceptions=False,
    headers={"Authorization": f"Bearer {API_KEY}"},
)


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_an_unknown_route_is_a_404_saying_which(method: str):
    response = http.request(method, "/v1/nope")

    assert response.status_code == 404
    assert response.json() == {
        "detail": f"no route for {method} /v1/nope; see /docs for the API's routes"
    }


def test_a_routes_own_404_is_unchanged():
    own = FastAPI()
    errors.install(own)

    @own.get("/things/{id}")
    def thing(id: str) -> None:
        raise HTTPException(404, detail="Not Found")

    response = TestClient(own).get("/things/1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_an_unsupported_method_is_a_405():
    response = http.get("/v1/omissions")

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize(
    "body",
    [
        b'{"source": "' + SECRET.encode() + b'", "summ',
        b"",
        b'["' + SECRET.encode() + b'"]',
        SECRET.encode() + bytes(range(256)),
    ],
    ids=["broken JSON", "empty", "a list", "binary"],
)
def test_a_malformed_request_is_a_4xx_not_a_crash(body: bytes):
    response = http.post(
        "/v1/omissions", content=body, headers={"content-type": "application/json"}
    )

    # FastAPI answers 400 for a body that isn't UTF-8, and 422 for the rest.
    assert response.status_code in (400, 422)
    assert "detail" in response.json()
    assert SECRET not in response.text


def test_an_error_outside_a_route_is_a_structured_500(
    caplog: pytest.LogCaptureFixture,
):
    def broken() -> None:
        raise RuntimeError(SECRET)

    app.dependency_overrides[api.model_client] = broken

    with caplog.at_level(logging.INFO):
        response = http.post("/v1/omissions", json={"source": "a", "summary": "b"})

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert "unexpected RuntimeError handling POST /v1/omissions" in caplog.text
    assert "in broken" in caplog.text
    assert SECRET not in response.text
    assert SECRET not in caplog.text


def test_a_safe_traceback_has_each_type_and_frame_but_no_message():
    try:
        try:
            raise ValueError(SECRET)
        except ValueError as inner:
            raise KeyError(SECRET) from inner
    except KeyError as error:
        text = safe_traceback(error)

    assert text.index("ValueError") < text.index("KeyError")
    assert "test_a_safe_traceback" in text
    assert SECRET not in text
