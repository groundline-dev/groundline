"""Real API-key lifecycle on the omissions route; only the model is faked."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from typer.testing import CliRunner

from groundline.api import common
from groundline.api import omissions as api
from groundline.app import app
from groundline.cli import app as cli
from groundline.db import api_keys
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE, SUMMARY, not_flagged_script

pytestmark = pytest.mark.db
runner = CliRunner()
BODY = {"source": SOURCE, "summary": SUMMARY}


@pytest.fixture
def model(db_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    client = FakeClient(not_flagged_script())
    monkeypatch.setitem(app.dependency_overrides, common.model_client, lambda: client)
    return client


@pytest.fixture
def evaluation(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spy = AsyncMock(wraps=api.evaluate)
    monkeypatch.setattr(api, "evaluate", spy)
    return spy


def test_cli_revocation_stops_the_next_omissions_request(
    db_engine: Engine,
    model: FakeClient,
    evaluation: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    created = runner.invoke(cli, ["keys", "create"])
    assert created.exit_code == 0, created.stderr
    match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
    assert match is not None
    credential = match[1]
    headers = {"Authorization": f"Bearer {credential}"}
    with db_engine.connect() as connection:
        identifier = connection.scalar(select(api_keys.c.id))
    assert identifier is not None

    with TestClient(app) as client:
        response = client.post("/v1/omissions", json=BODY, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["flagged"] is False
        assert body["usage"]["calls"] == 4
        assert len(model.requests) == 4
        assert credential not in response.text

        revoked = runner.invoke(cli, ["keys", "revoke", str(identifier)])
        assert revoked.exit_code == 0, revoked.stderr
        rejected = client.post("/v1/omissions", json=BODY, headers=headers)
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "Invalid or missing API key"}
        assert rejected.headers["www-authenticate"] == "Bearer"
        assert evaluation.await_count == 1
        assert len(model.requests) == 4
        assert client.get("/health").json() == {"status": "ok"}
    assert credential not in rejected.text + caplog.text


@pytest.mark.parametrize("state", ["unknown", "expired"])
def test_unknown_and_expired_api_keys_cannot_run_omissions(
    state: str,
    db_engine: Engine,
    model: FakeClient,
    evaluation: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    credential = "gl_" + "z" * 43
    if state == "expired":
        created = runner.invoke(cli, ["keys", "create"])
        assert created.exit_code == 0, created.stderr
        match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
        assert match is not None
        credential = match[1]

    with TestClient(app) as client:
        if state == "expired":
            # Expire after startup, without sleeps or a second application.
            with db_engine.begin() as connection:
                connection.execute(update(api_keys).values(expires_at=func.now()))
                assert connection.scalar(select(api_keys.c.status)) == "active"
        response = client.post(
            "/v1/omissions",
            json=BODY,
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid or missing API key"}
        assert response.headers["www-authenticate"] == "Bearer"
        evaluation.assert_not_called()
        assert model.requests == []
    assert credential not in response.text + caplog.text
