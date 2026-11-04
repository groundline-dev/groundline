import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from groundline.api import commissions, errors, omissions
from groundline.db import check_connection
from groundline.settings import Settings, UnreadableSettingsError, read_settings

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
    # Warnings, not failures: /health and the docs still work without a key,
    # and nothing needs the database yet.
    try:
        settings = read_settings()
    except UnreadableSettingsError as error:
        logging.getLogger("groundline.api").warning(
            "%s\nChecks will answer 503 until it's fixed.", error
        )
    else:
        await _check_setup(settings)
    yield


async def _check_setup(settings: Settings) -> None:
    if settings.anthropic_api_key is None:
        logging.getLogger("groundline.api").warning(
            "ANTHROPIC_API_KEY is not set, so checks will answer 503. "
            "Copy .env.example to .env and add your key."
        )
    if settings.database_url is None:
        logging.getLogger("groundline.db").warning(
            "DATABASE_URL is not set, so the API has no database"
        )
    else:
        await check_connection(settings.database_url.get_secret_value())


_log_to_console()
app = FastAPI(title="groundline", lifespan=lifespan)
errors.install(app)
app.include_router(omissions.router)
app.include_router(commissions.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
