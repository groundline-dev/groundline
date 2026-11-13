"""Shared API allowances and bounded request-body reading."""

from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from groundline.api import errors
from groundline.settings import APILimitsSettings


def _read_limits() -> APILimitsSettings:
    try:
        return APILimitsSettings()
    except ValidationError as error:
        # Validation locations are checked against known fields before naming
        # settings. Never include rejected values or a validation error chain.
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
    """Limit every HTTP request body, with groundline's JSON errors.

    FastAPI reads the entire body before parsing JSON. The native limiter
    bounds that read, checking both declared and actual byte counts. This
    adapter normalizes both native 413 paths, including its fallback response.
    It adds no pre-read or replay buffer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limited = RequestBodyLimitMiddleware(
            app, max_body_size=LIMITS.max_body_bytes
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        replaced = False

        async def json_send(message: Message) -> None:
            nonlocal replaced
            if message["type"] == "http.response.start" and message["status"] == 413:
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
                # FastAPI would turn an unexpected receive failure into 400.
                # Keep server failures distinct, without logging payload text.
                errors.logger.error(
                    "unexpected body read %s\n%s",
                    type(error).__name__,
                    errors.safe_traceback(error),
                )
                raise HTTPException(500, detail=errors.UNEXPECTED) from None

        await self.limited(scope, guarded_receive, json_send)
