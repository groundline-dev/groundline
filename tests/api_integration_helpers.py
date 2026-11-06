"""Opt-in fixtures for real settings/SDK integration with fake API-key storage."""

import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from groundline.app import app
from tests.auth_helpers import HEADERS, FakeKeys

ANTHROPIC_KEY = "smoke-anthropic-key"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> set[tuple[str, int]]:
    monkeypatch.chdir(tmp_path)  # Never read the developer's .env.
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    for variable in (
        "DATABASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(app, "dependency_overrides", {})

    # Intercepted transports need no sockets. HTTP tests opt into their mock's
    # exact loopback address/port after binding it; all other destinations fail.
    allowed: set[tuple[str, int]] = set()
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(address: object) -> None:
        if not any(address == destination for destination in allowed):
            raise AssertionError(
                f"integration test blocked network connection: {address}"
            )

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        check(address)
        connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        check(address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    return allowed


@pytest.fixture
def keys(isolated: set[tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> FakeKeys:
    storage = FakeKeys()
    monkeypatch.setattr(app.state, "api_keys", storage, raising=False)
    return storage


@pytest.fixture
def http(keys: FakeKeys) -> TestClient:
    # Deliberately skip the PostgreSQL-owning lifespan; storage is injected above.
    return TestClient(app, headers=HEADERS)
