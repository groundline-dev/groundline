"""Shared fake API-key storage, the protected check routes, and a test-only
protected route."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import Annotated
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI

from groundline_core.api import commissions, errors, omissions
from groundline_core.api_keys import APIKeyInfo, APIKeys
from groundline_core.app import health, lifespan
from groundline_core.auth import require_api_key
from tests.commissions_helpers import Script, issues_reply
from tests.omission_helpers import SOURCE, SUMMARY, not_flagged_script

SECRET = "gl_" + "a" * 43
HEADERS = {"Authorization": f"Bearer {SECRET}"}
BODY = {"source": SOURCE, "summary": SUMMARY}


@dataclass(frozen=True)
class Endpoint:
    path: str
    module: ModuleType
    """The route's module, where the tests spy on `evaluate`."""
    not_flagged: Callable[[], Callable[[str], str]]
    calls: int
    """The model calls one not-flagged check makes."""


ENDPOINTS = [
    Endpoint(
        "/v1/omissions",
        omissions,
        not_flagged_script,
        4,
    ),
    Endpoint(
        "/v1/commissions",
        commissions,
        lambda: Script(commissions_check=issues_reply()),
        1,
    ),
]


@pytest.fixture(params=ENDPOINTS, ids=lambda e: e.path)
def endpoint(request: pytest.FixtureRequest) -> Endpoint:
    return request.param


@pytest.fixture
def evaluation(endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spy = AsyncMock(wraps=endpoint.module.evaluate)
    monkeypatch.setattr(endpoint.module, "evaluate", spy)
    return spy


class FakeKeys(APIKeys):
    def __init__(self):
        self.calls: list[str] = []
        self.failure: Exception | None = None
        self.info: APIKeyInfo | None = APIKeyInfo(
            id=UUID("00000000-0000-7000-8000-000000000001"),
            name="test",
            prefix=SECRET[:11],
            status="active",
            created_at=datetime(2026, 11, 4, tzinfo=UTC),
            expires_at=None,
            revoked_at=None,
        )

    async def authenticate(self, credential: str) -> APIKeyInfo | None:
        self.calls.append(credential)
        if self.failure:
            raise self.failure
        return self.info if credential == SECRET else None


def protected_app(*, with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(lifespan=lifespan) if with_lifespan else FastAPI()
    errors.install(app)
    app.add_api_route("/health", health)

    @app.get("/protected")
    async def protected(
        api_key: Annotated[APIKeyInfo, Depends(require_api_key)],
    ) -> dict[str, str]:
        return {"api_key_id": str(api_key.id)}

    return app
