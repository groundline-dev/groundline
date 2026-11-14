"""Alembic's entry point. The database comes from scripts/db-setup, or from
DATABASE_URL when alembic is run directly."""

from alembic import context
from sqlalchemy import URL, create_engine, make_url

from groundline_core.db.tables import metadata
from groundline_core.settings import Settings


def _url() -> URL:
    given = context.config.attributes.get("database_url")
    if isinstance(given, URL):
        return given
    raw = Settings().database_url
    if raw is None:
        raise SystemExit("error: DATABASE_URL is not set")
    return make_url(raw.get_secret_value())


engine = create_engine(_url())
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
