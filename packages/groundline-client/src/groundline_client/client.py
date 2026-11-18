"""The one object a developer creates to call a groundline deployment's API."""

import math
import os
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from types import TracebackType
from typing import Any, Self

import httpx2

from ._generated.api.default import get_health, get_ready
from ._generated.api.v1 import evaluate_commissions, evaluate_omissions
from ._generated.client import AuthenticatedClient
from ._generated.models import (
    CommissionsRequest,
    CommissionsResponse,
    GetHealthResponseGetHealth,
    GetReadyResponseGetReady,
    OmissionsRequest,
    OmissionsResponse,
)
from ._http import AsyncErrorRaisingClient, ErrorRaisingClient, Limits
from .errors import GroundlineConfigError, GroundlineError

# Retries and time limits (see the README). An attempt may take 150 s because the
# server allows a check 120 s; the whole call, retries included, 180 s.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_RETRY_SECONDS = 180.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 150.0

URL_VARIABLE = "GROUNDLINE_API_URL"
API_KEY_VARIABLE = "GROUNDLINE_API_KEY"


def _is_usable_url(url: str) -> bool:
    """http or https, a valid host, no user name, password, query or fragment.

    A path such as `/api` is fine: the client adds each route after it.
    """
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
        and not parts.query
        and not parts.fragment
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


# What the generated code raises when a 2xx body isn't what it expects.
_UNREADABLE = (ValueError, KeyError, TypeError, AttributeError)
_UNREADABLE_ANSWER = "The API's answer was not in the expected form."


def _expect[Result](result: object, model: type[Result]) -> Result:
    """`result`, if the generated code parsed it as `model`; else an error.

    By now the answer was a 2xx. The generated code returns None for a 2xx it
    doesn't document, such as a 202.
    """
    if not isinstance(result, model):
        raise GroundlineError(_UNREADABLE_ANSWER)
    return result


def _call[Result](
    operation: Callable[..., object], model: type[Result], **arguments: Any
) -> Result:
    try:
        result = operation(**arguments)
    except _UNREADABLE:
        # Raised below, outside this block, so the error has no `__context__`.
        result = None
    return _expect(result, model)


async def _call_async[Result](
    operation: Callable[..., Awaitable[object]],
    model: type[Result],
    **arguments: Any,
) -> Result:
    try:
        result = await operation(**arguments)
    except _UNREADABLE:
        result = None
    return _expect(result, model)


def _is_positive_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:  # a whole number too big to convert to a float
        return False


def _option_problems(
    max_attempts: Any,
    max_retry_seconds: Any,
    connect_timeout_seconds: Any,
    timeout_seconds: Any,
) -> list[str]:
    """One message per bad retry or time-limit option. Never holds a value."""
    problems: list[str] = []
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        problems.append("max_attempts= must be a whole number of 1 or more.")
    seconds = {
        "max_retry_seconds": max_retry_seconds,
        "connect_timeout_seconds": connect_timeout_seconds,
        "timeout_seconds": timeout_seconds,
    }
    for name, value in seconds.items():
        if not _is_positive_finite_number(value):
            problems.append(f"{name}= must be a number of seconds above 0.")
    return problems


def _resolve_url_and_api_key(
    base_url: str | None, api_key: str | None, problems: list[str]
) -> tuple[str, str] | GroundlineConfigError:
    """The URL and API key to use, or the error to raise, saying which is wrong.

    `problems` holds what is already known to be wrong, and the error lists it
    too. The error is returned, not raised, so the API key is no longer a local
    variable of any frame in its traceback.
    """
    chosen_url = _choose(base_url, URL_VARIABLE, "base_url", problems)
    chosen_api_key = _choose(api_key, API_KEY_VARIABLE, "api_key", problems)
    if chosen_url is None:
        if base_url is None:
            problems.append(f"No API URL: pass base_url= or set {URL_VARIABLE}.")
    elif not _is_usable_url(chosen_url):
        problems.append(
            "The API URL must be an http:// or https:// address with a valid host "
            "and no user name, password, query or fragment "
            f"(from base_url= or {URL_VARIABLE})."
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
    """The generated client, with retries and time limits and a `repr` hiding the key.

    The generated client builds a plain `httpx2` client on first use; this one
    builds the retrying ones from `_http.py` instead, with the same arguments.
    Its `repr` would otherwise print the API key twice: as `token`, and in its
    headers after the first call. Tools that dump a traceback's local variables
    call `repr` on it.
    """

    def __init__(self, *, limits: Limits, **arguments: Any) -> None:
        super().__init__(**arguments)
        self._limits = limits

    def __repr__(self) -> str:
        return f"AuthenticatedClient(base_url={self._base_url!r}, token=<hidden>)"

    def _httpx_arguments(self) -> dict[str, Any]:
        self._headers[self.auth_header_name] = (
            f"{self.prefix} {self.token}" if self.prefix else self.token
        )
        return {
            "base_url": self._base_url,
            "cookies": self._cookies,
            "headers": self._headers,
            "timeout": self._timeout,
            "verify": self._verify_ssl,
            "follow_redirects": self._follow_redirects,
            **self._httpx_args,
        }

    def get_httpx_client(self) -> httpx2.Client:
        if self._client is None:
            self._client = ErrorRaisingClient(
                **asdict(self._limits), **self._httpx_arguments()
            )
        return self._client

    def get_async_httpx_client(self) -> httpx2.AsyncClient:
        if self._async_client is None:
            self._async_client = AsyncErrorRaisingClient(
                **asdict(self._limits), **self._httpx_arguments()
            )
        return self._async_client


class GroundlineClient:
    """Calls a groundline deployment's API with an API key.

    The URL and API key come from the arguments, or else from the environment
    variables `GROUNDLINE_API_URL` and `GROUNDLINE_API_KEY`; an argument wins
    over its variable. A missing, blank or unusable value raises
    `GroundlineConfigError` here, before any request is made.

    A temporary failure (a 408, 429, 502, 503 or 504 answer, a timeout or a
    connection error) is retried with a growing, randomised wait, honoring a
    `Retry-After` header. `max_attempts` counts the first request, so 1 turns
    retries off. `max_retry_seconds` limits the whole call, retries included, and
    `timeout_seconds` one attempt; `connect_timeout_seconds` limits setting up the
    connection. Retrying a check can repeat paid model work.

    Each operation has a sync method and an `_async` one. Use the client in a
    `with` block (or `async with`) to close its connections when you are done.
    A method returns the parsed result, and raises a `GroundlineError` (or one of
    its subclasses, see `errors.py`) for an error answer, a timeout, a
    connection failure, or an answer it can't read. A flagged summary is a
    result, not an error.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_retry_seconds: float = DEFAULT_MAX_RETRY_SECONDS,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        problems = _option_problems(
            max_attempts, max_retry_seconds, connect_timeout_seconds, timeout_seconds
        )
        outcome = _resolve_url_and_api_key(base_url, api_key, problems)
        # Tools that print a traceback's local variables would show the API key
        # in this frame, so drop the argument before raising.
        del api_key
        if isinstance(outcome, GroundlineConfigError):
            raise outcome
        self._base_url, resolved_api_key = outcome
        self._client = _ApiKeyHidingClient(
            base_url=self._base_url,
            token=resolved_api_key,
            limits=Limits(
                max_attempts=max_attempts,
                max_retry_seconds=max_retry_seconds,
                timeout_seconds=timeout_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
            ),
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

    def evaluate_omissions(self, body: OmissionsRequest) -> OmissionsResponse:
        """Find the facts a summary leaves out (`POST /v1/omissions`)."""
        return _call(
            evaluate_omissions.sync, OmissionsResponse, client=self._client, body=body
        )

    async def evaluate_omissions_async(
        self, body: OmissionsRequest
    ) -> OmissionsResponse:
        """Find the facts a summary leaves out (`POST /v1/omissions`)."""
        return await _call_async(
            evaluate_omissions.asyncio,
            OmissionsResponse,
            client=self._client,
            body=body,
        )

    def evaluate_commissions(self, body: CommissionsRequest) -> CommissionsResponse:
        """Find the claims the source doesn't support (`POST /v1/commissions`)."""
        return _call(
            evaluate_commissions.sync,
            CommissionsResponse,
            client=self._client,
            body=body,
        )

    async def evaluate_commissions_async(
        self, body: CommissionsRequest
    ) -> CommissionsResponse:
        """Find the claims the source doesn't support (`POST /v1/commissions`)."""
        return await _call_async(
            evaluate_commissions.asyncio,
            CommissionsResponse,
            client=self._client,
            body=body,
        )

    def health(self) -> GetHealthResponseGetHealth:
        """Whether the app is running (`GET /health`)."""
        return _call(get_health.sync, GetHealthResponseGetHealth, client=self._client)

    async def health_async(self) -> GetHealthResponseGetHealth:
        """Whether the app is running (`GET /health`)."""
        return await _call_async(
            get_health.asyncio, GetHealthResponseGetHealth, client=self._client
        )

    def ready(self) -> GetReadyResponseGetReady:
        """Whether the app can serve checks (`GET /ready`)."""
        return _call(get_ready.sync, GetReadyResponseGetReady, client=self._client)

    async def ready_async(self) -> GetReadyResponseGetReady:
        """Whether the app can serve checks (`GET /ready`)."""
        return await _call_async(
            get_ready.asyncio, GetReadyResponseGetReady, client=self._client
        )
