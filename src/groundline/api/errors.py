"""Error responses for every route: structured JSON, and nothing from the request.

An exception's message can quote the source, the summary or a secret, so
neither a response nor the log ever repeats one. The log gets the exception
types and stack frames instead, which hold only code.
"""

import logging
import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("groundline.api")

UNEXPECTED = "unexpected error; see the server log"


def install(app: FastAPI) -> None:
    """Give `app` the error responses below."""
    app.middleware("http")(_catch_unexpected)
    # Starlette types every handler as taking a plain Exception.
    app.add_exception_handler(RequestValidationError, _invalid_request)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, _http_error)  # pyright: ignore[reportArgumentType]


def safe_traceback(error: BaseException) -> str:
    """Each exception in the chain, outermost last: its type and its frames,
    without its message."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    parts = [
        "".join(traceback.format_tb(e.__traceback__)) + type(e).__name__
        for e in reversed(chain)
    ]
    return "\n".join(parts)


async def _catch_unexpected(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Any exception a route doesn't handle: a generic 500, not a crash, and a
    log line uvicorn would otherwise write with the exception's message."""
    try:
        return await call_next(request)
    except Exception as error:
        logger.error(
            "unexpected %s handling %s %s\n%s",
            type(error).__name__,
            request.method,
            request.url.path,
            safe_traceback(error),
        )
        return JSONResponse(status_code=500, content={"detail": UNEXPECTED})


async def _invalid_request(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    """FastAPI's default 422 repeats each rejected value, which here could be a
    whole transcript. Say where and what was wrong, never the value."""
    detail = [
        {"loc": _safe_loc(e), "msg": e["msg"], "type": e["type"]}
        for e in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def _safe_loc(error: Mapping[str, Any]) -> list[Any]:
    """An unexpected field's location ends in its name, which came from the
    request, so it stops at the object holding it."""
    loc = list(error["loc"])
    return loc[:-1] if error["type"] == "extra_forbidden" else loc


async def _http_error(request: Request, error: StarletteHTTPException) -> Response:
    """A request for a route that doesn't exist says which, and where the routes
    are listed. Other HTTP errors, including a route's own 404s, are unchanged."""
    # Starlette sets the endpoint only once a route has matched.
    if error.status_code == 404 and "endpoint" not in request.scope:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"no route for {request.method} {request.url.path}; "
                "see /docs for the API's routes"
            },
        )
    return await http_exception_handler(request, error)
