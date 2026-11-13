"""PostgreSQL fixtures, used only by the tests that ask for them.

They never touch the developer's database. They log in with DATABASE_ADMIN_URL
(from the environment or .env, pointing at the Compose Postgres) and create a
throwaway login and database for each test module.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, make_url

from groundline.db import api_keys
from groundline.db.setup import drop_database, setup_database
from groundline.settings import Settings

# API limits are loaded while test modules import the app. Keep inherited
# operator overrides out of the suite; override tests use fresh processes.
for _limit_name in (
    "GROUNDLINE_MAX_BODY_BYTES",
    "GROUNDLINE_MAX_SOURCE_CHARS",
    "GROUNDLINE_MAX_SUMMARY_CHARS",
):
    os.environ.pop(_limit_name, None)


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = Settings().database_admin_url
    if url is None:
        pytest.fail(
            "DATABASE_ADMIN_URL is not set; the database tests need it to create "
            "throwaway databases. Start Postgres with `make db` and see .env.example."
        )
    return url.get_secret_value()


@pytest.fixture(scope="module")
def db_url(admin_url: str) -> Iterator[str]:
    name = f"groundline_test_{uuid.uuid4().hex[:8]}"
    server = make_url(admin_url)
    url = server.set(
        username=name, password=f"pw-{name}", database=name
    ).render_as_string(hide_password=False)
    setup_database(url, admin_url)
    try:
        yield url
    finally:
        drop_database(url, admin_url)


@pytest.fixture
def db_engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(api_keys.delete())
        engine.dispose()
