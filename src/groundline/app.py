import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from groundline.api import errors, omissions
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
errors.install(app)
app.include_router(omissions.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
