"""Alembic's entry point. The database comes from scripts/db-setup, or from
DATABASE_URL when alembic is run directly."""

from alembic import context
from sqlalchemy import URL, create_engine, make_url

from groundline_core.core.errors import SetupError
from groundline_core.db.tables import metadata
from groundline_core.settings import ENV_FILE, read_settings


def _url() -> URL:
    given = context.config.attributes.get("database_url")
    if isinstance(given, URL):
        return given
    try:
        raw = read_settings(ENV_FILE).database_url
    except SetupError as error:
        raise SystemExit(f"error: {error}") from None
    if raw is None:
        raise SystemExit("error: DATABASE_URL is not set")
    return make_url(raw.get_secret_value())


engine = create_engine(_url())
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
