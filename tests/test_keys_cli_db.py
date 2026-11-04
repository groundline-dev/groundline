"""Exercise the real CLI and database across separate event-loop lifetimes."""

import hashlib
import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, update
from typer.testing import CliRunner

from groundline.cli import app
from groundline.db import api_keys

pytestmark = pytest.mark.db
runner = CliRunner()


def test_list_shows_prefixes_and_states_without_credentials_or_hashes(
    db_url: str,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    empty = runner.invoke(app, ["keys", "list"])
    assert empty.exit_code == 0
    assert empty.stdout == "No API keys found.\n"
    credentials: list[str] = []
    for name in ("revoked", "expired", "active"):
        created = runner.invoke(app, ["keys", "create", "--name", name])
        assert created.exit_code == 0
        match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
        assert match is not None
        credentials.append(match[1])
    with db_engine.begin() as connection:
        connection.execute(
            update(api_keys)
            .where(api_keys.c.name == "expired")
            .values(expires_at=func.now())
        )
        identifier = connection.scalar(
            select(api_keys.c.id).where(api_keys.c.name == "revoked")
        )
    assert runner.invoke(app, ["keys", "revoke", str(identifier)]).exit_code == 0
    listed = runner.invoke(app, ["keys", "list"])
    assert listed.exit_code == 0
    rows = [re.split(r" {2,}", line) for line in listed.stdout.splitlines()[1:]]
    assert [row[1] for row in rows] == ["active", "expired", "revoked"]
    assert [row[3] for row in rows] == ["active", "expired", "revoked"]
    with db_engine.connect() as connection:
        stored = connection.execute(select(api_keys)).mappings().all()
    for row in stored:
        assert str(row["id"]) in listed.stdout
        assert row["prefix"] in listed.stdout
        assert row["key_hash"] not in listed.output
    for credential in credentials:
        secret_absent = credential not in listed.output
        assert secret_absent


def test_create_and_repeat_revoke_commands(
    db_url: str,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    created = runner.invoke(app, ["keys", "create", "--expires-in", "90d"])
    assert created.exit_code == 0, created.stderr
    match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
    assert match is not None
    credential = match[1]
    with db_engine.connect() as connection:
        row = connection.execute(select(api_keys)).mappings().one()
    identifier = row["id"]
    assert isinstance(identifier, UUID)
    assert str(identifier) in created.stdout
    assert row["key_hash"] == hashlib.sha256(credential.encode()).hexdigest()
    assert row["expires_at"] - row["created_at"] == timedelta(days=90)
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9a-f]{8}", row["name"])
    for expected in ("Revoked:", "Already revoked:"):
        result = runner.invoke(app, ["keys", "revoke", str(identifier)])
        assert result.exit_code == 0, result.stderr
        assert expected in result.stdout
        assert credential not in result.output
    with db_engine.connect() as connection:
        row = connection.execute(select(api_keys)).mappings().one()
    assert row["status"] == "revoked"
    assert row["revoked_at"] is not None
