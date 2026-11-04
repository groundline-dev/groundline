import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from groundline.api import errors, omissions
from groundline.api_keys import APIKeys
from groundline.core.errors import SetupError
from groundline.db import check_connection
from groundline.db.connection import make_engine
from groundline.settings import UnreadableSettingsError, read_settings

logger = logging.getLogger("groundline")
api_logger = logging.getLogger("groundline.api")
db_logger = logging.getLogger("groundline.db")


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


def _database_url() -> str | None:
    """The database URL from the settings, warning about anything missing that
    leaves checks or API-key storage unavailable."""
    try:
        settings = read_settings()
    except UnreadableSettingsError as error:
        api_logger.warning("%s\nChecks will answer 503 until it's fixed.", error)
        return None
    if settings.anthropic_api_key is None:
        api_logger.warning(
            "ANTHROPIC_API_KEY is not set, so checks will answer 503. "
            "Copy .env.example to .env and add your key."
        )
    if settings.database_url is None:
        db_logger.warning("DATABASE_URL is not set, so the API has no database")
        return None
    return settings.database_url.get_secret_value()


def _storage_engine(url: str) -> AsyncEngine | None:
    try:
        return make_engine(url)
    except SetupError as error:
        db_logger.error("API-key storage is unavailable: %s", error)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Public health stays available when settings or storage are unavailable.
    engine: AsyncEngine | None = None
    app.state.api_keys = None
    url = _database_url()
    try:
        if url is not None and (engine := _storage_engine(url)) is not None:
            await check_connection(url, engine=engine)
            # Keep the engine after a failed probe so later requests can
            # recover when PostgreSQL becomes available.
            app.state.api_keys = APIKeys(engine)
        yield
    finally:
        app.state.api_keys = None
        if engine is not None:
            await engine.dispose()


_log_to_console()
app = FastAPI(title="groundline", lifespan=lifespan)
errors.install(app)
app.include_router(omissions.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
