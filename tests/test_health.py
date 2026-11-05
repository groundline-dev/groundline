import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text

from groundline.app import app

client = TestClient(app)

# Nothing listens on port 1, so the connection is refused at once.
UNREACHABLE = "postgresql+psycopg://groundline:pw-secret@127.0.0.1:1/groundline"


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real DATABASE_URL and .env out of these tests."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.usefixtures("isolated")
def test_without_a_database_ready_is_503_and_health_is_ok():
    with TestClient(app) as running:
        assert running.get("/ready").status_code == 503
        assert running.get("/health").status_code == 200


@pytest.mark.usefixtures("isolated")
def test_an_unreachable_database_is_503_without_details(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)

    with TestClient(app) as running:
        ready = running.get("/ready")
        health = running.get("/health")

    assert ready.status_code == 503
    for detail in ("127.0.0.1", "pw-secret", "groundline", "refused"):
        assert detail not in ready.text
    assert health.status_code == 200


@pytest.mark.usefixtures("isolated")
def test_repeated_failed_checks_log_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Polling during an outage doesn't repeat the same error line."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)

    with TestClient(app) as running:
        caplog.clear()  # startup's own connection check may log the outage
        with caplog.at_level(logging.INFO, logger="groundline"):
            statuses = [running.get("/ready").status_code for _ in range(3)]

    assert statuses == [503, 503, 503]
    problems = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(problems) <= 1


@pytest.mark.db
def test_ready_is_200_when_the_database_answers(
    db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)

    with TestClient(app) as running:
        assert running.get("/ready").status_code == 200


@pytest.mark.db
def test_ready_recovers_at_once_after_postgres_drops_the_connections(
    db_url: str, admin_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Dropping the connections stands in for a Postgres restart. The pool
    replaces dead connections, so the next request succeeds without restarting
    the app."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    role = make_url(db_url).username

    with TestClient(app) as running:
        assert running.get("/ready").status_code == 200  # pools a connection
        admin = create_engine(admin_url)
        try:
            with admin.begin() as connection:
                dropped = connection.scalar(
                    text(
                        "SELECT count(pg_terminate_backend(pid)) "
                        "FROM pg_stat_activity WHERE usename = :role"
                    ),
                    {"role": role},
                )
        finally:
            admin.dispose()
        assert dropped, "the app held no connection to drop"

        assert running.get("/ready").status_code == 200


@pytest.mark.db
def test_an_outage_logs_once_when_it_starts_and_once_when_it_ends(
    db_url: str,
    admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """/ready logs only when the database goes down or comes back, so an outage
    leaves one warning and one info line however often /ready is polled."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    role = make_url(db_url).username
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    def set_login(allowed: bool) -> None:
        # Blocking the role's login and ending its connections cuts the app off
        # from the database, as an outage would, and leaves the data alone.
        with admin.connect() as connection:
            connection.execute(
                text(f'ALTER ROLE "{role}" {"LOGIN" if allowed else "NOLOGIN"}')
            )
            if not allowed:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE usename = :role"
                    ),
                    {"role": role},
                )

    def poll(times: int) -> tuple[list[int], list[logging.LogRecord]]:
        caplog.clear()
        statuses = [running.get("/ready").status_code for _ in range(times)]
        return statuses, [r for r in caplog.records if r.name.startswith("groundline")]

    try:
        with caplog.at_level(logging.INFO, logger="groundline"):
            with TestClient(app) as running:
                assert poll(2)[0] == [200, 200]

                set_login(False)
                statuses, went_down = poll(3)
                assert statuses == [503, 503, 503]
                assert [r.levelno >= logging.WARNING for r in went_down] == [True]

                set_login(True)
                statuses, came_back = poll(3)
                assert statuses == [200, 200, 200]
                assert [r.levelno for r in came_back] == [logging.INFO]
    finally:
        set_login(True)
        admin.dispose()
