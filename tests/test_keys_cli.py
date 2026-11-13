import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner

from groundline_core import cli, keys_cli
from groundline_core.api_keys import (
    APIKeyInfo,
    APIKeyNotFoundError,
    APIKeys,
    APIKeyStorageError,
    CreatedAPIKey,
    RevokedAPIKey,
)
from groundline_core.db.connection import make_engine

runner = CliRunner()
IDENTIFIER = UUID("00000000-0000-7000-8000-000000000001")
SECRET = "gl_" + "a" * 43


class FakeKeys(APIKeys):
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.failure: Exception | None = None
        self.changed = True
        self.listed: list[APIKeyInfo] = []

    async def create(
        self, *, name: str | None = None, expires_in: timedelta | None = None
    ) -> CreatedAPIKey:
        self.calls.append(("create", (name, expires_in)))
        if self.failure:
            raise self.failure
        created = datetime(2026, 11, 4, tzinfo=UTC)
        info = APIKeyInfo(
            id=IDENTIFIER,
            name=name or "default",
            prefix=SECRET[:11],
            status="active",
            created_at=created,
            revoked_at=None,
            expires_at=created + expires_in if expires_in else None,
        )
        return CreatedAPIKey(info, SecretStr(SECRET))

    async def revoke(self, identifier: UUID) -> RevokedAPIKey:
        self.calls.append(("revoke", identifier))
        if self.failure:
            raise self.failure
        info = (await self.create(name="laptop")).info.model_copy(
            update={
                "status": "revoked",
                "revoked_at": datetime(2026, 11, 4, tzinfo=UTC),
            }
        )
        return RevokedAPIKey(info, self.changed)

    async def list_all(self) -> list[APIKeyInfo]:
        self.calls.append(("list", None))
        if self.failure:
            raise self.failure
        return self.listed


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeKeys:
    keys = FakeKeys()

    async def run[T](operation: Callable[[APIKeys], Awaitable[T]]) -> T:
        return await operation(keys)

    monkeypatch.setattr(keys_cli, "_run", run)
    return keys


def test_create_displays_identifier_expiry_and_secret_once(fake: FakeKeys):
    result = runner.invoke(
        cli.app, ["keys", "create", "--name", " laptop ", "--expires-in", "90d"]
    )
    assert result.exit_code == 0, result.output
    assert fake.calls == [("create", ("laptop", timedelta(days=90)))]
    assert f"ID: {IDENTIFIER}" in result.stdout
    assert "Expires: 2027-02-02T00:00:00+00:00" in result.stdout
    assert result.stdout.count(SECRET) == 1
    assert "cannot be retrieved later" in result.stdout
    assert result.stderr == ""


def test_defaults_generate_a_name_and_never_expire(fake: FakeKeys):
    result = runner.invoke(cli.app, ["keys", "create"])
    assert result.exit_code == 0
    assert re.search(r"Name: [a-z]+-[a-z]+-[0-9a-f]{8}", result.stdout)
    assert "Expires: never" in result.stdout
    assert len(fake.calls) == 1


def test_name_length_boundary_and_trimming(fake: FakeKeys):
    name = "x" * 128
    accepted = runner.invoke(cli.app, ["keys", "create", "--name", f"  {name}  "])
    assert accepted.exit_code == 0
    assert fake.calls == [("create", (name, None))]
    fake.calls.clear()
    rejected = runner.invoke(cli.app, ["keys", "create", "--name", name + "x"])
    assert rejected.exit_code == 2
    assert "at most 128" in rejected.stderr
    assert rejected.stdout == ""
    assert name not in rejected.stderr
    assert fake.calls == []


@pytest.mark.parametrize("duration,seconds", [("1s", 1), ("30m", 1800), ("12h", 43200)])
def test_duration_units(fake: FakeKeys, duration: str, seconds: int):
    result = runner.invoke(
        cli.app, ["keys", "create", "--name", "test", "--expires-in", duration]
    )
    assert result.exit_code == 0
    assert fake.calls == [("create", ("test", timedelta(seconds=seconds)))]


@pytest.mark.parametrize(
    "value", ["0d", "-1d", "1.5d", "1h30m", "never", "90", "9" * 30 + "d"]
)
def test_invalid_durations_fail_before_configuration_or_storage(value: str):
    result = runner.invoke(cli.app, ["keys", "create", "--expires-in", value])
    assert result.exit_code == 2
    assert "--expires-in" in result.stderr
    assert "DATABASE_URL" not in result.stderr
    assert result.stdout == ""


def test_invalid_name_and_identifier_fail_before_storage():
    for args in (["create", "--name", "\n"], ["revoke", "not-a-uuid"]):
        result = runner.invoke(cli.app, ["keys", *args])
        assert result.exit_code == 2
        assert "DATABASE_URL" not in result.stderr
        assert result.stdout == ""


@pytest.mark.parametrize("command", ["create", "list"])
def test_missing_url_is_helpful_and_help_needs_no_database(command: str):
    result = runner.invoke(cli.app, ["keys", command])
    assert result.exit_code == 2
    assert "DATABASE_URL is not set" in result.stderr
    assert "DEVELOPMENT.md" in result.stderr
    assert result.stdout == ""
    assert runner.invoke(cli.app, ["keys", "--help"]).exit_code == 0


@pytest.mark.parametrize("command", ["create", "list"])
def test_unreadable_env_names_the_fix_without_quoting_it(command: str):
    Path(".env").write_bytes(b"DATABASE_URL=postgresql://gl:\xffsecret@localhost/db")
    result = runner.invoke(cli.app, ["keys", command])
    assert result.exit_code == 2
    assert "can't read the settings (UnicodeDecodeError)" in result.stderr
    assert "readable UTF-8 text file" in result.stderr
    assert "secret" not in result.output
    assert result.stdout == ""


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://gl:secret@:x/db",
        "sqlite:///secret",
        "postgresql+unknown://gl:secret@localhost/db",
    ],
)
def test_bad_database_url_is_sanitized(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("DATABASE_URL", value)
    result = runner.invoke(cli.app, ["keys", "create"])
    assert result.exit_code == 2
    assert "DATABASE_URL" in result.stderr
    assert "secret" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("changed", [True, False])
def test_revocation_is_successful_when_already_revoked(fake: FakeKeys, changed: bool):
    fake.changed = changed
    result = runner.invoke(cli.app, ["keys", "revoke", str(IDENTIFIER)])
    assert result.exit_code == 0
    assert ("Revoked:" if changed else "Already revoked:") in result.stdout
    assert fake.calls[0] == ("revoke", IDENTIFIER)
    assert SECRET not in result.output


def test_unknown_identifier_is_an_error(fake: FakeKeys):
    fake.failure = APIKeyNotFoundError("API-key identifier was not found")
    result = runner.invoke(cli.app, ["keys", "revoke", str(IDENTIFIER)])
    assert result.exit_code == 2
    assert "not found" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("failure", [RuntimeError(SECRET), ValueError(SECRET)])
def test_unexpected_failures_never_echo_their_details(
    fake: FakeKeys, failure: Exception
):
    fake.failure = failure
    result = runner.invoke(cli.app, ["keys", "create"])
    assert result.exit_code == 2
    assert SECRET not in result.output
    assert result.stdout == ""
    assert "API-key operation failed" in result.stderr


def test_output_failure_reports_the_committed_identifier(
    fake: FakeKeys,
    monkeypatch: pytest.MonkeyPatch,
):
    errors: list[str] = []

    def echo(message: str, *, err: bool = False) -> None:
        if not err:
            raise OSError(SECRET)
        errors.append(message)

    monkeypatch.setattr(keys_cli.typer, "echo", echo)
    result = runner.invoke(cli.app, ["keys", "create"])
    assert result.exit_code == 2
    assert len(fake.calls) == 1  # no automatic retry after commit
    assert str(IDENTIFIER) in errors[0]
    assert SECRET not in errors[0]


@pytest.mark.parametrize("command", ["create", "list"])
def test_engine_is_disposed_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch, command: str
):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:secret@127.0.0.1:1/db"
    )
    disposed: list[bool] = []

    def factory(raw: str) -> AsyncEngine:
        engine = make_engine(raw, timeout=1)

        def on_disposed(_engine: object) -> None:
            disposed.append(True)

        event.listen(engine.sync_engine, "engine_disposed", on_disposed)
        return engine

    monkeypatch.setattr(keys_cli, "make_engine", factory)
    result = runner.invoke(cli.app, ["keys", command])
    assert result.exit_code == 2
    assert "secret" not in result.output
    assert result.stdout == ""
    assert disposed == [True]


def test_list_empty_is_successful(fake: FakeKeys):
    result = runner.invoke(cli.app, ["keys", "list"])
    assert result.exit_code == 0
    assert result.stdout == "No API keys found.\n"
    assert fake.calls == [("list", None)]


def test_list_displays_safe_metadata_and_expired_status(fake: FakeKeys):
    now = datetime.now(UTC)
    base = APIKeyInfo(
        id=IDENTIFIER,
        name="laptop",
        prefix=SECRET[:11],
        status="active",
        created_at=now - timedelta(days=2),
        revoked_at=None,
        expires_at=None,
    )
    expired = base.model_copy(
        update={"name": "PC", "expires_at": now - timedelta(days=1)}
    )
    future = base.model_copy(
        update={
            "name": "production reporting service",
            "expires_at": now + timedelta(days=1),
        }
    )
    revoked = expired.model_copy(update={"status": "revoked", "revoked_at": now})
    fake.listed = [base, expired, future, revoked]
    result = runner.invoke(cli.app, ["keys", "list"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert re.split(r" {2,}", lines[0]) == [
        "ID",
        "Name",
        "Prefix",
        "Status",
        "Expires (UTC)",
    ]
    assert "\t" not in result.stdout
    rows = [re.split(r" {2,}", line) for line in lines[1:]]
    assert [row[3] for row in rows] == ["active", "expired", "active", "revoked"]
    assert rows[0] == [str(IDENTIFIER), "laptop", SECRET[:11], "active", "never"]
    assert rows[1][4] == (now - timedelta(days=1)).isoformat()
    assert expired.status == "active"  # Display does not mutate stored metadata.
    assert SECRET not in result.output
    assert "key_hash" not in result.output
    assert {line.index(SECRET[:11]) for line in lines[1:]} == {lines[0].index("Prefix")}
    assert {line.index(row[3]) for line, row in zip(lines[1:], rows)} == {
        lines[0].index("Status")
    }


def test_list_aligns_wide_and_combining_names_without_truncating(fake: FakeKeys):
    now = datetime.now(UTC)
    for name in ("PC", "工作电脑", "Cafe\u0301", "x" * 128):
        fake.listed.append(
            APIKeyInfo(
                id=IDENTIFIER,
                name=name,
                prefix=SECRET[:11],
                status="active",
                created_at=now,
                revoked_at=None,
                expires_at=None,
            )
        )
    result = runner.invoke(cli.app, ["keys", "list"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    prefix_column = lines[0].index("Prefix")
    # UUID occupies 36 columns plus two spaces. Display widths here are known
    # independently: PC=2, four CJK characters=8, decomposed Café=4, x*128=128.
    for line, name, width in zip(
        lines[1:], ("PC", "工作电脑", "Cafe\u0301", "x" * 128), (2, 8, 4, 128)
    ):
        assert str(IDENTIFIER) in line
        assert name in line
        padding = line[38 + len(name) : line.index(SECRET[:11])]
        assert padding == " " * (prefix_column - 38 - width)


@pytest.mark.parametrize(
    "failure,message",
    [
        (
            APIKeyStorageError("API-key storage is unavailable"),
            "storage is unavailable",
        ),
        (RuntimeError(SECRET), "API-key operation failed"),
    ],
)
def test_list_errors_are_sanitized(fake: FakeKeys, failure: Exception, message: str):
    fake.failure = failure
    result = runner.invoke(cli.app, ["keys", "list"])
    assert result.exit_code == 2
    assert message in result.stderr
    assert result.stdout == ""
    assert SECRET not in result.output
    assert "Traceback" not in result.output
