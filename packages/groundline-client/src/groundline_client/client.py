"""The one object a developer creates to call a groundline deployment's API."""

import os
import warnings
from types import TracebackType
from typing import Self

import httpx2

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


def _is_usable_url(url: str) -> bool:
    """http or https, with a valid host and no user name or password."""
    try:
        parts = httpx2.URL(url)
    except (httpx2.InvalidURL, ValueError):
        return False
    # httpx2 percent-encodes characters a host can't hold, such as a space or
    # a stray "[", instead of rejecting them, so a "%" in the host means a typo.
    return (
        parts.scheme in ("http", "https")
        and bool(parts.host)
        and "%" not in parts.host
        and not parts.userinfo
    )


def _choose(
    argument: str | None, variable: str, name: str, problems: list[str]
) -> str | None:
    """The argument, else the environment variable, else None.

    A blank argument is a problem, not a reason to use the variable: it usually
    means a secret that didn't load. A blank variable counts as not set.
    """
    if argument is not None:
        if argument.strip():
            return argument.strip()
        problems.append(
            f"{name}= is blank. Pass a value, or leave it out to use {variable}."
        )
        return None
    return os.environ.get(variable, "").strip() or None


def _resolve_url_and_api_key(
    base_url: str | None, api_key: str | None
) -> tuple[str, str] | GroundlineConfigError:
    """The URL and API key to use, or the error to raise, saying which is wrong.

    The error is returned, not raised, so the API key is no longer a local
    variable of any frame in its traceback.
    """
    problems: list[str] = []
    chosen_url = _choose(base_url, URL_VARIABLE, "base_url", problems)
    chosen_api_key = _choose(api_key, API_KEY_VARIABLE, "api_key", problems)
    if chosen_url is None:
        if base_url is None:
            problems.append(f"No API URL: pass base_url= or set {URL_VARIABLE}.")
    elif not _is_usable_url(chosen_url):
        problems.append(
            "The API URL must be an http:// or https:// address with a valid host "
            f"and no user name or password (from base_url= or {URL_VARIABLE})."
        )
    if chosen_api_key is None:
        if api_key is None:
            problems.append(f"No API key: pass api_key= or set {API_KEY_VARIABLE}.")
    elif not (chosen_api_key.isascii() and chosen_api_key.isprintable()):
        # Sending it would fail with an error that holds the whole header value.
        problems.append(
            "The API key has a character that can't be sent in an HTTP header "
            f"(from api_key= or {API_KEY_VARIABLE})."
        )
    if problems or chosen_url is None or chosen_api_key is None:
        return GroundlineConfigError(" ".join(problems))
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
    over its variable. A missing, blank or unusable value raises
    `GroundlineConfigError` here, before any request is made.

    Each operation has a sync method and an `_async` one. Use the client in a
    `with` block (or `async with`) to close its connections when you are done.
    An error answer comes back as the parsed error body, and an answer the API
    doesn't document comes back as None.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        outcome = _resolve_url_and_api_key(base_url, api_key)
        # Tools that print a traceback's local variables would show the API key
        # in this frame, so drop the argument before raising.
        del api_key
        if isinstance(outcome, GroundlineConfigError):
            raise outcome
        self._base_url, resolved_api_key = outcome
        self._client = _ApiKeyHidingClient(
            base_url=self._base_url, token=resolved_api_key
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
        async_http = self._client._async_client  # pyright: ignore[reportPrivateUsage]
        if async_http is not None and not async_http.is_closed:
            warnings.warn(
                "This client made async calls inside a plain `with` block, which "
                "can't close the async connection. Use `async with` instead.",
                RuntimeWarning,
                stacklevel=2,
            )
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
        # Close the sync connection too, if a sync call made one: closing a
        # client that was never used would build one just to throw it away.
        try:
            await self._client.__aexit__(exc_type, exc, traceback)
        finally:
            if self._client._client is not None:  # pyright: ignore[reportPrivateUsage]
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
