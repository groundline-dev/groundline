"""Exercise real Uvicorn scheme handling for Caddy's private-network requests."""

from typing import cast

import pytest
from starlette.testclient import TestClient
from starlette.types import ASGIApp
from uvicorn import Config


@pytest.mark.parametrize("trusted,scheme", [(True, "https"), (False, "http")])
def test_private_proxy_scheme_controls_application_redirects(
    monkeypatch: pytest.MonkeyPatch, trusted: bool, scheme: str
):
    if trusted:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "172.30.254.2")
    else:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    config = Config("groundline.app:app", log_config=None)
    config.load()
    # No lifespan is needed for the public docs route. The remote IP models
    # Caddy on Docker's bridge, outside Uvicorn's default loopback trust.
    client = TestClient(
        cast(ASGIApp, getattr(config, "loaded_app")),
        base_url="http://203.0.113.10",
        client=("172.30.254.2", 50000),
    )
    try:
        response = client.get(
            "/docs/", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
        )
        assert response.status_code == 307
        assert response.headers["location"] == f"{scheme}://203.0.113.10/docs"
    finally:
        client.close()


@pytest.mark.parametrize(
    "peer,expected_client,expected_scheme",
    [
        ("172.30.254.2", "203.0.113.7", "https"),
        ("172.30.254.3", "172.30.254.3", "http"),
    ],
)
def test_only_caddy_can_supply_client_identity(
    peer: str, expected_client: str, expected_scheme: str
):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from uvicorn._types import ASGI3Application
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    async def identity(request: Request) -> JSONResponse:
        assert request.client is not None
        return JSONResponse(
            {"client": request.client.host, "scheme": request.url.scheme}
        )

    backend = ProxyHeadersMiddleware(
        cast(ASGI3Application, Starlette(routes=[Route("/", identity)])),
        trusted_hosts="172.30.254.2",
    )
    with TestClient(cast(ASGIApp, backend), client=(peer, 50000)) as client:
        response = client.get(
            "/",
            headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
        )
    assert response.json() == {
        "client": expected_client,
        "scheme": expected_scheme,
    }
