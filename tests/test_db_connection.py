"""Connection checks need no database: these point at an address nothing listens on."""

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundline.app import app
from groundline.db import check_connection

UNREACHABLE = "postgresql+psycopg://groundline:pw-secret@127.0.0.1:1/groundline"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real DATABASE_URL and .env out of these tests."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)


def test_a_failed_connection_is_logged_without_the_password(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.INFO, logger="groundline.db"):
        connected = asyncio.run(check_connection(UNREACHABLE))

    assert connected is False
    (record,) = caplog.records
    assert record.name == "groundline.db" and record.levelno == logging.ERROR
    assert "can't connect to the database at 127.0.0.1:1/groundline" in (
        record.getMessage()
    )
    assert "pw-secret" not in caplog.text


def test_a_malformed_url_is_logged_without_the_password(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.INFO, logger="groundline.db"):
        connected = asyncio.run(check_connection("postgresql://gl:pw-secret@:x/"))

    assert connected is False
    assert "can't connect to the database" in caplog.text
    assert "pw-secret" not in caplog.text


def test_a_plain_postgresql_url_uses_psycopg():
    # SQLAlchemy would look for psycopg2 for postgresql://, which isn't installed.
    plain = UNREACHABLE.replace("postgresql+psycopg", "postgresql")

    assert asyncio.run(check_connection(plain)) is False  # refused, not ImportError


def test_the_api_starts_and_logs_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)

    with caplog.at_level(logging.INFO, logger="groundline"), TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert "can't connect to the database at 127.0.0.1:1/groundline" in caplog.text
    assert "pw-secret" not in caplog.text


def test_the_api_starts_without_a_database_url_and_says_so(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.INFO, logger="groundline"), TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert "DATABASE_URL is not set" in caplog.text
