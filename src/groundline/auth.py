"""Opt-in authentication for protected routes; public routes need no database."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from groundline.api_keys import APIKeyInfo, APIKeys, valid_api_key_format

logger = logging.getLogger("groundline.auth")
_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> APIKeyInfo:
    """Return safe metadata, or reject without disclosing credential state."""
    if credentials is None or not valid_api_key_format(credentials.credentials):
        raise _unauthorized()
    keys = getattr(request.app.state, "api_keys", None)
    if not isinstance(keys, APIKeys):
        raise HTTPException(status_code=503, detail="Authentication is unavailable")
    try:
        info = await keys.authenticate(credentials.credentials)
    except Exception as error:
        # Even an unexpected error must not put credentials or driver details in
        # an HTTP response or a server traceback. Never log the exception here.
        logger.error("API-key authentication is unavailable: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Authentication is unavailable"
        ) from None
    if info is None:
        raise _unauthorized()
    return info
