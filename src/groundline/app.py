import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import APIRouter, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
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


DESCRIPTION = """
groundline checks an AI-written summary, such as a clinical note, against the
source it was written from, such as a consultation transcript. It reports the
facts the summary leaves out (omissions) and the statements in it the source
doesn't support (commissions). It is research code, **not for clinical use**,
and must not be used with real patient data yet.

### Authentication

Both checks need a groundline API key, sent as `Authorization: Bearer gl_...`.
Create one with `groundline keys create`, then use **Authorize** below.
`/health` is public.

### Versions

Both checks are under `/v1`. Within v1, a change only adds:
new endpoints, or new fields in a response. A change that could break a
caller, such as removing or renaming a field or changing a status code, gets a
new prefix, `/v2`. groundline is still pre-1.0, so it doesn't yet promise how
long an old version stays available after a new one ships. The version next to
the title is groundline's release, not the API version.

See the [README](https://github.com/groundline-dev/groundline#using-the-api)
for more, including what each status means.
"""

_log_to_console()
app = FastAPI(
    title="groundline",
    version=version("groundline"),
    description=DESCRIPTION,
    openapi_tags=[
        {"name": "v1", "description": "The checks, version 1. Each needs an API key."}
    ],
    lifespan=lifespan,
)
errors.install(app)
v1 = APIRouter(prefix="/v1", tags=["v1"])
v1.include_router(omissions.router)
v1.include_router(commissions.router)
app.include_router(v1)


@app.get("/api", include_in_schema=False)
def api_docs(request: Request) -> HTMLResponse:
    """The interactive API docs: the same page as /docs, built the way FastAPI
    builds it, including the path prefix of an app behind a proxy."""
    assert app.openapi_url is not None  # the docs are generated from it
    root = request.scope.get("root_path", "").rstrip("/")
    redirect = app.swagger_ui_oauth2_redirect_url
    return get_swagger_ui_html(
        openapi_url=root + app.openapi_url,
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url=root + redirect if redirect else None,
        init_oauth=app.swagger_ui_init_oauth,
        swagger_ui_parameters=app.swagger_ui_parameters,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
