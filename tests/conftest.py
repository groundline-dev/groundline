"""Shared, opt-in PostgreSQL fixtures; never use the developer's database."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine

from groundline.db import api_keys
from groundline.db.setup import drop_database, setup_database


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    name = f"groundline_test_{uuid.uuid4().hex[:8]}"
    url = f"postgresql+psycopg://{name}:pw-{name}@localhost:5432/{name}"
    setup_database(url)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture
def db_engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(api_keys.delete())
        engine.dispose()
