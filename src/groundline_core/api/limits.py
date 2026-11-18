"""Shared API allowances and bounded request-body reading."""

from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.body_limit import RequestBodyLimitResponder
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from groundline_core._settings import APILimitsSettings
from groundline_core.api import errors


def _read_limits() -> APILimitsSettings:
    try:
        return APILimitsSettings()
    except ValidationError as error:
        # Report known setting names without exposing rejected values.
        names = sorted(
            {
                "GROUNDLINE_" + str(problem["loc"][0]).upper()
                for problem in error.errors()
                if problem["loc"]
                and problem["loc"][0] in APILimitsSettings.model_fields
            }
        )
        raise ValueError(
            "Invalid API size setting: "
            + ", ".join(names)
            + "; use a positive ASCII decimal integer"
        ) from None


LIMITS = _read_limits()
BODY_TOO_LARGE = "Request body exceeds the configured size limit"


class BodyLimitMiddleware:
    """Enforce Starlette's body limit with safe JSON errors."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limiter = RequestBodyLimitResponder(self.app, LIMITS.max_body_bytes)
        replaced = False

        async def json_send(message: Message) -> None:
            nonlocal replaced
            # A route's own 413 must retain its body and headers.
            body_exceeded = limiter.total_size > limiter.max_body_size or (
                limiter.content_length is not None
                and limiter.content_length > limiter.max_body_size
            )
            if (
                message["type"] == "http.response.start"
                and message["status"] == 413
                and body_exceeded
            ):
                replaced = True
                await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(
                    scope, receive, send
                )
            elif not replaced:
                await send(message)

        async def guarded_receive() -> Message:
            try:
                return await receive()
            except (ClientDisconnect, HTTPException):
                raise
            except Exception as error:
                # FastAPI otherwise turns unexpected read failures into 400.
                errors.logger.error(
                    "unexpected body read %s\n%s",
                    type(error).__name__,
                    errors.safe_traceback(error),
                )
                raise HTTPException(500, detail=errors.UNEXPECTED) from None

        await limiter(scope, guarded_receive, json_send)
