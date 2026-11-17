"""The one object a developer creates to call a groundline deployment's API."""

import os
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from ._generated.api.default import get_health, get_ready
from ._generated.api.v1 import evaluate_commissions, evaluate_omissions
from ._generated.client import AuthenticatedClient
from ._generated.models import (
    CommissionsRequest,
    CommissionsResponse,
    ErrorBody,
    GetHealthResponseGetHealth,
    GetReadyResponseGetReady,
    InvalidRequestBody,
    OmissionsRequest,
    OmissionsResponse,
)
from .errors import GroundlineConfigError

URL_VARIABLE = "GROUNDLINE_API_URL"
API_KEY_VARIABLE = "GROUNDLINE_API_KEY"


def _argument_or_variable(argument: str | None, variable: str) -> str | None:
    """The argument, else the environment variable, else None; blank is None."""
    for value in (argument, os.environ.get(variable)):
        if value is not None and value.strip():
            return value.strip()
    return None


def resolve_url_and_api_key(
    base_url: str | None, api_key: str | None
) -> tuple[str, str]:
    """Return the URL and API key to use, or raise saying which is wrong."""
    chosen_url = _argument_or_variable(base_url, URL_VARIABLE)
    chosen_api_key = _argument_or_variable(api_key, API_KEY_VARIABLE)
    problems: list[str] = []
    if chosen_url is None:
        problems.append(f"No API URL: pass base_url= or set {URL_VARIABLE}.")
    else:
        parts = urlsplit(chosen_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            problems.append(
                "The API URL must start with http:// or https:// "
                f"(from base_url= or {URL_VARIABLE})."
            )
    if chosen_api_key is None:
        problems.append(f"No API key: pass api_key= or set {API_KEY_VARIABLE}.")
    if problems or chosen_url is None or chosen_api_key is None:
        raise GroundlineConfigError(" ".join(problems))
    return chosen_url, chosen_api_key


class _ApiKeyHidingClient(AuthenticatedClient):
    """The generated client, whose `repr` would otherwise print the API key.

    It shows the API key twice: as `token`, and in its headers after the first
    call. Tools that dump a traceback's local variables call `repr` on it.
    """

    def __repr__(self) -> str:
        return f"AuthenticatedClient(base_url={self._base_url!r}, token=<hidden>)"


class GroundlineClient:
    """Calls a groundline deployment's API with an API key.

    The URL and API key come from the arguments, or else from the environment
    variables `GROUNDLINE_API_URL` and `GROUNDLINE_API_KEY`; an argument wins
    over its variable. A missing or unusable value raises
    `GroundlineConfigError` here, before any request is made.

    Each operation has a sync method and an `_async` one. Use the client in a
    `with` block (or `async with`) to close its connections when you are done.
    An error answer comes back as the parsed error body, and an answer the API
    doesn't document comes back as None.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        resolved_url, resolved_api_key = resolve_url_and_api_key(base_url, api_key)
        self._base_url = resolved_url
        self._client = _ApiKeyHidingClient(
            base_url=resolved_url, token=resolved_api_key
        )

    def __repr__(self) -> str:
        return f"GroundlineClient(base_url={self._base_url!r})"

    def __enter__(self) -> Self:
        self._client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.__exit__(exc_type, exc, traceback)

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Close the sync connection too: a block can use both kinds of method.
        try:
            await self._client.__aexit__(exc_type, exc, traceback)
        finally:
            self._client.__exit__(exc_type, exc, traceback)

    def evaluate_omissions(
        self, body: OmissionsRequest
    ) -> ErrorBody | InvalidRequestBody | OmissionsResponse | None:
        """Find the facts a summary leaves out (`POST /v1/omissions`)."""
        return evaluate_omissions.sync(client=self._client, body=body)

    async def evaluate_omissions_async(
        self, body: OmissionsRequest
    ) -> ErrorBody | InvalidRequestBody | OmissionsResponse | None:
        """Find the facts a summary leaves out (`POST /v1/omissions`)."""
        return await evaluate_omissions.asyncio(client=self._client, body=body)

    def evaluate_commissions(
        self, body: CommissionsRequest
    ) -> ErrorBody | InvalidRequestBody | CommissionsResponse | None:
        """Find the claims the source doesn't support (`POST /v1/commissions`)."""
        return evaluate_commissions.sync(client=self._client, body=body)

    async def evaluate_commissions_async(
        self, body: CommissionsRequest
    ) -> ErrorBody | InvalidRequestBody | CommissionsResponse | None:
        """Find the claims the source doesn't support (`POST /v1/commissions`)."""
        return await evaluate_commissions.asyncio(client=self._client, body=body)

    def health(self) -> ErrorBody | GetHealthResponseGetHealth | None:
        """Whether the app is running (`GET /health`)."""
        return get_health.sync(client=self._client)

    async def health_async(self) -> ErrorBody | GetHealthResponseGetHealth | None:
        """Whether the app is running (`GET /health`)."""
        return await get_health.asyncio(client=self._client)

    def ready(self) -> ErrorBody | GetReadyResponseGetReady | None:
        """Whether the app can serve checks (`GET /ready`)."""
        return get_ready.sync(client=self._client)

    async def ready_async(self) -> ErrorBody | GetReadyResponseGetReady | None:
        """Whether the app can serve checks (`GET /ready`)."""
        return await get_ready.asyncio(client=self._client)
