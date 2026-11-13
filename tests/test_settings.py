from pathlib import Path

import pytest

from groundline.settings import MissingAPIKeyError, Settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real key and .env out of these tests."""
    for name in (*OPERATOR_DEFAULTS, *INTERNAL_LIMITS):
        monkeypatch.delenv("GROUNDLINE_" + name.upper(), raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in (
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
        "GROUNDLINE_DB_PASSWORD",
        "POSTGRES_HOST_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    assert Settings().anthropic_key() == "sk-env"


def test_reads_key_from_dotenv_file():
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    assert Settings().anthropic_key() == "sk-dotenv"


def test_environment_wins_over_dotenv_file(monkeypatch: pytest.MonkeyPatch):
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    assert Settings().anthropic_key() == "sk-env"


def test_missing_key_raises_a_helpful_error():
    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
        Settings().anthropic_key()


def test_empty_key_counts_as_missing():
    # .env.example ships with an empty ANTHROPIC_API_KEY= line.
    Path(".env").write_text("ANTHROPIC_API_KEY=\n")

    with pytest.raises(MissingAPIKeyError):
        Settings().anthropic_key()


def test_key_is_hidden_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    settings = Settings()

    assert "sk-secret" not in repr(settings)
    assert "sk-secret" not in str(settings)


def test_missing_key_message_says_what_needs_it_and_where_it_looked():
    with pytest.raises(MissingAPIKeyError) as error:
        Settings().anthropic_key(needed_by="extract, rank (claude-opus-5-5)")

    message = str(error.value)
    assert message.startswith("ANTHROPIC_API_KEY is not set.")
    assert "Needed by: extract, rank (claude-opus-5-5)" in message
    assert (
        f"Looked in: the environment, and {Path.cwd() / '.env'} (not found)" in message
    )
    assert "copy .env.example to .env" in message


def test_missing_key_message_notes_a_dotenv_file_without_the_key():
    Path(".env").write_text("OTHER_KEY=x\n")

    with pytest.raises(MissingAPIKeyError, match=r"\.env \(found, but it has no"):
        Settings().anthropic_key()


def test_reports_where_the_key_came_from(monkeypatch: pytest.MonkeyPatch):
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")
    assert Settings().anthropic_key_source() == f"{Path.cwd() / '.env'}"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert Settings().anthropic_key_source() == "the environment"


def test_reads_the_database_url_and_hides_it():
    Path(".env").write_text(
        "DATABASE_URL=postgresql+psycopg://gl:pw-secret@localhost/gl\n"
    )
    settings = Settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().endswith("@localhost/gl")
    assert "pw-secret" not in repr(settings)


def test_the_database_url_is_optional():
    assert Settings().database_url is None


def test_reads_the_admin_url_as_a_secret(monkeypatch: pytest.MonkeyPatch):
    url = "postgresql+psycopg://postgres:admin-secret@localhost:5433/postgres"
    monkeypatch.setenv("DATABASE_ADMIN_URL", url)

    settings = Settings()

    assert settings.database_admin_url is not None
    assert settings.database_admin_url.get_secret_value() == url
    assert "admin-secret" not in repr(settings)


def test_a_dotenv_url_can_reuse_a_password_set_above_it():
    """In .env each password is set once and the URLs refer to it with ${...},
    the same way compose.yaml does."""
    Path(".env").write_text(
        "GROUNDLINE_DB_PASSWORD=0f1e2d3c\n"
        "DATABASE_URL=postgresql+psycopg://groundline:${GROUNDLINE_DB_PASSWORD}"
        "@localhost:5433/groundline\n"
    )

    url = Settings().database_url

    assert url is not None
    assert url.get_secret_value() == (
        "postgresql+psycopg://groundline:0f1e2d3c@localhost:5433/groundline"
    )


@pytest.mark.parametrize(
    "port_line,port", [("POSTGRES_HOST_PORT=6543\n", "6543"), ("", "5433")]
)
def test_a_dotenv_url_follows_the_postgres_port(port_line: str, port: str):
    """Changing POSTGRES_HOST_PORT in .env also changes the port in DATABASE_URL."""
    Path(".env").write_text(
        port_line + "DATABASE_URL=postgresql+psycopg://groundline:pw"
        "@localhost:${POSTGRES_HOST_PORT:-5433}/groundline\n"
    )

    url = Settings().database_url

    assert url is not None
    assert f"@localhost:{port}/" in url.get_secret_value()


OPERATOR_DEFAULTS = {"check_timeout_seconds": 120, "model_max_attempts": 3}
INTERNAL_LIMITS = (
    "db_auth_timeout_seconds",
    "db_connect_timeout_seconds",
    "db_pool_timeout_seconds",
    "db_query_timeout_seconds",
    "db_check_timeout_seconds",
    "db_readiness_timeout_seconds",
    "model_timeout_seconds",
    "model_connect_timeout_seconds",
    "model_retry_timeout_seconds",
    "model_retry_base_seconds",
    "model_retry_cap_seconds",
)


def test_retry_and_timeout_defaults():
    settings = Settings()
    for name, default in OPERATOR_DEFAULTS.items():
        assert getattr(settings, name) == default


@pytest.mark.parametrize("name", ["check_timeout_seconds", "model_max_attempts"])
def test_retry_and_timeout_settings_can_be_overridden(
    name: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GROUNDLINE_" + name.upper(), "2")
    assert getattr(Settings(), name, None) == 2


@pytest.mark.parametrize("name", ["check_timeout_seconds", "model_max_attempts"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "nonsense"])
def test_retry_and_timeout_settings_reject_unbounded_or_invalid_values(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch
):
    from pydantic import ValidationError

    monkeypatch.setenv("GROUNDLINE_" + name.upper(), value)
    with pytest.raises(ValidationError):
        Settings()


def test_attempt_count_must_be_an_integer(monkeypatch: pytest.MonkeyPatch):
    from pydantic import ValidationError

    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", "2.5")
    with pytest.raises(ValidationError):
        Settings()


def test_copying_env_example_preserves_application_timeout_defaults():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    Path(".env").write_text(example.read_text())
    settings = Settings()
    for name, default in OPERATOR_DEFAULTS.items():
        assert getattr(settings, name) == default


def test_timeout_override_can_use_dotenv_or_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    Path(".env").write_text("GROUNDLINE_CHECK_TIMEOUT_SECONDS=30\n")
    assert Settings().check_timeout_seconds == 30
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "60")
    assert Settings().check_timeout_seconds == 60


def test_internal_limits_ignore_environment_and_dotenv(
    monkeypatch: pytest.MonkeyPatch,
):
    defaults = {name: getattr(Settings(), name) for name in INTERNAL_LIMITS}
    Path(".env").write_text(
        "\n".join(f"GROUNDLINE_{name.upper()}=nonsense" for name in INTERNAL_LIMITS)
    )
    for name, default in defaults.items():
        assert getattr(Settings(), name) == default
        monkeypatch.setenv("GROUNDLINE_" + name.upper(), "0.001")
    settings = Settings()
    for name, default in defaults.items():
        assert getattr(settings, name) == default
        assert name not in Settings.model_fields
