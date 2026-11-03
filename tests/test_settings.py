from pathlib import Path

import pytest

from groundline.settings import MissingAPIKeyError, Settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real key and .env out of these tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
