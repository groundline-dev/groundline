import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from groundline.db import check_connection
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
    # Nothing needs the database yet, so the API starts whether or not it's up.
    url = Settings().database_url
    if url is None:
        logging.getLogger("groundline.db").warning(
            "DATABASE_URL is not set, so the API has no database"
        )
    else:
        await check_connection(url.get_secret_value())
    yield


_log_to_console()
app = FastAPI(title="groundline", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
