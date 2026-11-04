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
    # Public health stays available when settings or storage are unavailable.
    engine: AsyncEngine | None = None
    app.state.api_keys = None
    try:
        try:
            settings = read_settings()
        except UnreadableSettingsError as error:
            logging.getLogger("groundline.api").warning(
                "%s\nChecks will answer 503 until it's fixed.", error
            )
        else:
            if settings.anthropic_api_key is None:
                logging.getLogger("groundline.api").warning(
                    "ANTHROPIC_API_KEY is not set, so checks will answer 503. "
                    "Copy .env.example to .env and add your key."
                )
            url = settings.database_url
            if url is None:
                logging.getLogger("groundline.db").warning(
                    "DATABASE_URL is not set, so the API has no database"
                )
            else:
                try:
                    engine = make_engine(url.get_secret_value())
                except SetupError as error:
                    logging.getLogger("groundline.db").error(
                        "API-key storage is unavailable: %s", error
                    )
                else:
                    await check_connection(url.get_secret_value(), engine=engine)
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
