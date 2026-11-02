from pathlib import Path

import pytest

from groundline.settings import MissingAPIKeyError, Settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real key and .env out of these tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
