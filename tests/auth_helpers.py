"""Shared fake API-key storage and a test-only protected route."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI

from groundline.api import errors
from groundline.api_keys import APIKeyInfo, APIKeys
from groundline.app import health, lifespan
from groundline.auth import require_api_key

SECRET = "gl_" + "a" * 43


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
