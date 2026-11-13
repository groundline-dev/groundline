from pathlib import Path

import pytest
from pydantic import SecretStr

from groundline_core.settings import (
    ENV_FILE,
    MissingAPIKeyError,
    Settings,
    UnreadableSettingsError,
    anthropic_key,
    read_settings,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real key and .env out of these tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in (
        "DATABASE_URL",
        "DATABASE_ADMIN_URL",
        "GROUNDLINE_DB_PASSWORD",
        "POSTGRES_HOST_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


FROM_ARGUMENT = "the anthropic_api_key argument"


def test_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    key = anthropic_key(None, env_file=None)

    assert key.value.get_secret_value() == "sk-env"
    assert key.source == "the environment"
    assert key.name == "ANTHROPIC_API_KEY"


def test_reads_key_from_the_env_file_it_is_given():
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    key = anthropic_key(None, env_file=ENV_FILE)

    assert key.value.get_secret_value() == "sk-dotenv"
    assert key.source == str(Path.cwd() / ".env")


def test_an_env_file_can_be_anywhere(tmp_path: Path):
    elsewhere = tmp_path / "config" / "groundline.env"
    elsewhere.parent.mkdir()
    elsewhere.write_text("ANTHROPIC_API_KEY=sk-elsewhere\n")

    key = anthropic_key(None, env_file=elsewhere)

    assert key.value.get_secret_value() == "sk-elsewhere"
    assert key.source == str(elsewhere)


def test_without_an_env_file_no_dotenv_is_read():
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    with pytest.raises(MissingAPIKeyError):
        anthropic_key(None, env_file=None)
    assert read_settings(None).anthropic_api_key is None


def test_environment_wins_over_env_file(monkeypatch: pytest.MonkeyPatch):
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    key = anthropic_key(None, env_file=ENV_FILE)

    assert key.value.get_secret_value() == "sk-env"
    assert key.source == "the environment"


@pytest.mark.parametrize("argument", ["sk-arg", SecretStr("sk-arg")])
def test_the_argument_wins_over_environment_and_env_file(
    monkeypatch: pytest.MonkeyPatch, argument: str | SecretStr
):
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    key = anthropic_key(argument, env_file=ENV_FILE)

    assert key.value.get_secret_value() == "sk-arg"
    assert key.source == FROM_ARGUMENT
    assert key.name == "The Anthropic API key"


@pytest.mark.parametrize("blank", ["", "   ", SecretStr(""), SecretStr(" \t")])
def test_a_blank_argument_is_missing_even_when_the_environment_has_a_key(
    monkeypatch: pytest.MonkeyPatch, blank: str | SecretStr
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    with pytest.raises(MissingAPIKeyError):
        anthropic_key(blank, env_file=None)


def test_a_blank_argument_says_so_even_with_an_env_file():
    Path(".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\n")

    with pytest.raises(MissingAPIKeyError) as error:
        anthropic_key(" ", env_file=ENV_FILE, needed_by="extract (claude-opus-5-5)")

    expected = (
        "The anthropic_api_key argument is blank.\n"
        "  Needed by: extract (claude-opus-5-5)\n"
        "  Fix: pass the Anthropic API key, or leave anthropic_api_key out "
        "to use ANTHROPIC_API_KEY."
    )
    assert str(error.value) == expected
    assert error.value.api_message() == expected


def test_missing_key_raises_a_helpful_error():
    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
        anthropic_key(None, env_file=ENV_FILE)


def test_empty_key_counts_as_missing():
    # .env.example ships with an empty ANTHROPIC_API_KEY= line.
    Path(".env").write_text("ANTHROPIC_API_KEY=\n")

    with pytest.raises(MissingAPIKeyError):
        anthropic_key(None, env_file=ENV_FILE)


def test_key_is_hidden_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    settings = read_settings(None)

    assert "sk-secret" not in repr(settings)
    assert "sk-secret" not in str(settings)


def test_the_chosen_key_is_hidden_when_printed():
    key = anthropic_key("sk-ant-sentinel", env_file=None)

    assert "sentinel" not in repr(key)
    assert "sentinel" not in str(key)


def test_missing_key_message_says_what_needs_it_and_where_it_looked():
    with pytest.raises(MissingAPIKeyError) as error:
        anthropic_key(
            None, env_file=ENV_FILE, needed_by="extract, rank (claude-opus-5-5)"
        )

    message = str(error.value)
    assert message.startswith("ANTHROPIC_API_KEY is not set.")
    assert "Needed by: extract, rank (claude-opus-5-5)" in message
    assert (
        f"Looked in: the environment, and {Path.cwd() / '.env'} (not found)" in message
    )
    assert "copy .env.example to .env" in message
    assert str(Path.cwd()) not in error.value.api_message()


def test_missing_key_message_notes_a_dotenv_file_without_the_key():
    Path(".env").write_text("OTHER_KEY=x\n")

    with pytest.raises(MissingAPIKeyError, match=r"\.env \(found, but it has no"):
        anthropic_key(None, env_file=ENV_FILE)


def test_the_fix_names_the_env_file_the_caller_gave(tmp_path: Path):
    with pytest.raises(MissingAPIKeyError, match=r"copy \.env\.example to app\.env"):
        anthropic_key(None, env_file=tmp_path / "app.env")


def test_without_an_env_file_the_missing_key_message_names_no_path():
    with pytest.raises(MissingAPIKeyError) as error:
        anthropic_key(None, env_file=None, needed_by="extract (claude-opus-5-5)")

    expected = (
        "The Anthropic API key is not set.\n"
        "  Needed by: extract (claude-opus-5-5)\n"
        "  Looked in: the anthropic_api_key argument, "
        "and ANTHROPIC_API_KEY in the environment\n"
        "  Fix: pass anthropic_api_key=..., or set ANTHROPIC_API_KEY."
    )
    assert str(error.value) == expected
    assert error.value.api_message() == expected


def test_unreadable_settings_name_the_env_file_only_in_the_full_message():
    Path(".env").write_bytes(b"ANTHROPIC_API_KEY=\xffsk-ant-sentinel")

    with pytest.raises(UnreadableSettingsError) as error:
        read_settings(ENV_FILE)

    assert str(Path.cwd() / ".env") in str(error.value)
    assert str(Path.cwd()) not in error.value.api_message()
    assert "sentinel" not in str(error.value)
    assert "sentinel" not in error.value.api_message()


def test_unreadable_settings_without_an_env_file_name_no_file():
    error = UnreadableSettingsError("ValidationError", None)

    assert str(error) == (
        "can't read the settings (ValidationError).\n"
        "  Fix: check the environment variables groundline reads."
    )
    assert error.api_message() == str(error)


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
