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
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    else:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    config = Config("groundline.app:app", log_config=None)
    config.load()
    # No lifespan is needed for the public docs route. The remote IP models
    # Caddy on Docker's bridge, outside Uvicorn's default loopback trust.
    client = TestClient(
        cast(ASGIApp, getattr(config, "loaded_app")),
        base_url="http://203.0.113.10",
        client=("172.18.0.3", 50000),
    )
    try:
        response = client.get(
            "/docs/", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False
        )
        assert response.status_code == 307
        assert response.headers["location"] == f"{scheme}://203.0.113.10/docs"
    finally:
        client.close()
