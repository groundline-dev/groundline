import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from groundline.api import omissions
from groundline.settings import Settings

logger = logging.getLogger("groundline")


class _UvicornStyle(logging.Formatter):
    """Pads the level like uvicorn does ("INFO:     "), so the lines align."""

    def format(self, record: logging.LogRecord) -> str:
        record.prefix = f"{record.levelname}:".ljust(9)
        return super().format(record)


def _log_to_console() -> None:
    """Show groundline's log lines alongside uvicorn's. Uvicorn only sets up its
    own loggers, so without this only warnings and errors would appear, bare."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_UvicornStyle("%(prefix)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # A warning, not a failure: /health and the docs still work without a key.
    if Settings().anthropic_api_key is None:
        logging.getLogger("groundline.api").warning(
            "ANTHROPIC_API_KEY is not set, so checks will answer 503. "
            "Copy .env.example to .env and add your key."
        )
    yield


_log_to_console()
app = FastAPI(title="groundline", lifespan=lifespan)
app.include_router(omissions.router)


@app.exception_handler(RequestValidationError)
async def validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    """FastAPI's default 422 repeats each rejected value, which here could be a
    whole transcript. Say where and what was wrong, never the value."""
    detail = [
        {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
