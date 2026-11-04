import asyncio
import re
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from groundline import api_keys
from groundline.api_keys import APIKeys, APIKeyStorageError


def test_generation_requests_256_random_bits_and_hides_the_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    requested: list[int | None] = []

    def token_urlsafe(nbytes: int | None = None) -> str:
        requested.append(nbytes)
        return "a" * 43

    monkeypatch.setattr(api_keys.secrets, "token_urlsafe", token_urlsafe)
    secret = api_keys.generate_api_key()
    assert requested == [32]
    assert secret.get_secret_value() == "gl_" + "a" * 43
    assert secret.get_secret_value() not in repr(secret)


def test_real_generated_credentials_match_the_public_format():
    secret = api_keys.generate_api_key().get_secret_value()
    assert len(secret) >= 32
    assert api_keys.valid_api_key_format(secret)


def test_generated_names_and_custom_names():
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9a-f]{8}", api_keys.api_key_name(None))
    assert api_keys.api_key_name("  Avi's laptop  ") == "Avi's laptop"


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "a\r", "\tlabel", "a\x1bb"])
def test_invalid_names_are_rejected(name: str):
    with pytest.raises(ValueError, match="name"):
        api_keys.api_key_name(name)


def test_invalid_inputs_do_not_need_storage():
    async def run():
        engine = create_async_engine("postgresql+psycopg://user:secret@127.0.0.1:1/db")
        try:
            service = APIKeys(engine)
            assert await service.authenticate("malformed-secret") is None
            for lifetime in (timedelta(0), timedelta(seconds=-1)):
                with pytest.raises(ValueError, match="positive"):
                    await service.create(expires_in=lifetime)
            with pytest.raises(ValueError, match="name"):
                await service.create(name="\n")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_connection_failure_is_sanitized():
    async def run():
        engine = create_async_engine(
            "postgresql+psycopg://user:password-secret@127.0.0.1:1/db",
            connect_args={"connect_timeout": 1},
        )
        try:
            with pytest.raises(APIKeyStorageError) as error:
                await APIKeys(engine).create()
            assert str(error.value) == "API-key storage is unavailable"
            assert error.value.__suppress_context__
        finally:
            await engine.dispose()

    asyncio.run(run())
