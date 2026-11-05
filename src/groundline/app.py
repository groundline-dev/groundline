import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from groundline.api import commissions, errors, omissions
from groundline.api_keys import APIKeys
from groundline.core.errors import SetupError
from groundline.db import check_connection
from groundline.db.connection import make_engine
from groundline.settings import UnreadableSettingsError, read_settings

logger = logging.getLogger("groundline")
api_logger = logging.getLogger("groundline.api")
db_logger = logging.getLogger("groundline.db")


class _Database:
    """The app's database engine, plus whether it answered the last check: the
    startup check, then each /ready check.

    Something may poll /ready every few seconds. Because the last result is
    kept, a check logs only when the database goes down or comes back,
    instead of logging the same error on every poll."""

    def __init__(self, url: str, engine: AsyncEngine, connected: bool) -> None:
        self.url = url
        self.engine = engine
        self.connected = connected

    async def check(self) -> bool:
        self.connected = await check_connection(
            self.url, engine=self.engine, was_connected=self.connected
        )
        return self.connected


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
    app.state.database = None
    url = _database_url()
    try:
        if url is not None and (engine := _storage_engine(url)) is not None:
            connected = await check_connection(url, engine=engine)
            # Keep the engine after a failed probe so later requests can
            # recover when PostgreSQL becomes available.
            app.state.api_keys = APIKeys(engine)
            app.state.database = _Database(url, engine, connected)
        yield
    finally:
        app.state.api_keys = None
        app.state.database = None
        if engine is not None:
            await engine.dispose()


_log_to_console()
app = FastAPI(title="groundline", lifespan=lifespan)
errors.install(app)
app.include_router(omissions.router)
app.include_router(commissions.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Whether the app is running. It never touches the database, so an outage
    doesn't mark a working app unhealthy in Compose."""
    return {"status": "ok"}


@app.get("/ready", responses={503: {"description": "The database isn't answering."}})
async def ready(request: Request) -> dict[str, str]:
    """Whether the app can serve requests that need the database."""
    database: _Database | None = getattr(request.app.state, "database", None)
    if database is None or not await database.check():
        raise HTTPException(503, detail="the database isn't available")
    return {"status": "ok"}
