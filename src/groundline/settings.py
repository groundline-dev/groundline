"""Secrets and environment settings, read from the environment or a `.env` file.

Values already in the environment win over `.env`. Keys are held as `SecretStr`
so they never appear when settings are printed or logged.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingAPIKeyError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,  # a blank `ANTHROPIC_API_KEY=` counts as unset
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = None

    def anthropic_key(self) -> str:
        """The Anthropic API key, or a clear error if it isn't set.

        Only live model calls need a key, so a missing one is reported here,
        when it's needed, rather than when settings are loaded.
        """
        if self.anthropic_api_key is None:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add it to your environment or to .env "
                "(copy .env.example)."
            )
        return self.anthropic_api_key.get_secret_value()
