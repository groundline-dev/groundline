"""The client as a context manager closes its connections (R6, ALL-156)."""

import asyncio
from typing import Any

import groundline_client as sdk
import pytest
from conftest import FakeServer


def make_client(fake_server: FakeServer, api_key: str) -> Any:
    return sdk.GroundlineClient(base_url=fake_server.url, api_key=api_key)


def http_clients(client: Any) -> tuple[Any, Any]:
    """The sync and async HTTP clients inside, which are None until first used."""
    generated = client._client
    return generated._client, generated._async_client


def test_with_gives_back_the_same_client(fake_server: FakeServer, api_key: str):
    client = make_client(fake_server, api_key)
    with client as entered:
        assert entered is client


def test_leaving_a_with_block_closes_the_sync_connection(
    fake_server: FakeServer, api_key: str
):
    with make_client(fake_server, api_key) as client:
        client.health()
        sync_http, _ = http_clients(client)
        assert not sync_http.is_closed
    assert sync_http.is_closed


def test_a_with_block_closes_the_connection_even_when_its_body_raises(
    fake_server: FakeServer, api_key: str
):
    captured: list[Any] = []
    with pytest.raises(RuntimeError, match="boom"):
        with make_client(fake_server, api_key) as client:
            client.health()
            captured.append(http_clients(client)[0])
            raise RuntimeError("boom")
    assert captured[0].is_closed


def test_leaving_an_async_with_block_closes_the_sync_and_the_async_connections(
    fake_server: FakeServer, api_key: str
):
    async def use() -> tuple[Any, Any]:
        async with make_client(fake_server, api_key) as client:
            client.health()
            await client.health_async()
            sync_http, async_http = http_clients(client)
            assert not sync_http.is_closed
            assert not async_http.is_closed
        return sync_http, async_http

    sync_http, async_http = asyncio.run(use())
    assert sync_http.is_closed
    assert async_http.is_closed


def test_an_async_with_block_closes_the_connections_even_when_its_body_raises(
    fake_server: FakeServer, api_key: str
):
    async def use() -> Any:
        async with make_client(fake_server, api_key) as client:
            await client.health_async()
            _, async_http = http_clients(client)
            try:
                raise RuntimeError("boom")
            finally:
                captured.append(async_http)

    captured: list[Any] = []
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(use())
    assert captured[0].is_closed


def test_leaving_a_block_that_made_no_call_does_not_fail(
    fake_server: FakeServer, api_key: str
):
    with make_client(fake_server, api_key):
        pass
