"""A protected route for tests, without adding a production endpoint."""

from typing import Annotated

from fastapi import Depends, FastAPI

from groundline.api import errors
from groundline.api_keys import APIKeyInfo
from groundline.app import health, lifespan
from groundline.auth import require_api_key


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
