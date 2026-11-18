"""Turn an answer, or a failure to get one, into the SDK's error.

Plain functions that take no request or response, only the status, the headers
and the body bytes, so the error they build can't hold the API key (the
request's headers do). The classes are in `errors.py`; `_http.py` calls these.
"""

import json
import time
from collections.abc import Iterable, Mapping
from typing import cast

import httpx2

from ._retry import retry_after_seconds
from .errors import (
    AuthenticationError,
    GroundlineError,
    GroundlineTimeoutError,
    InvalidRequestError,
    RateLimitError,
    RequestTooLargeError,
    ServerUnavailableError,
)

# Each status the SDK knows, with the class and the fixed wording for it. The
# statuses are the API's own (400, 401, 413, 422, 500, 502, 503, 504) and the
# ones a proxy in front of it adds (403, 408, 429).
_BY_STATUS: dict[int, tuple[type[GroundlineError], str]] = {
    400: (InvalidRequestError, "The API rejected the request as invalid"),
    401: (AuthenticationError, "The API refused the API key"),
    403: (AuthenticationError, "The API refused the API key"),
    408: (GroundlineTimeoutError, "The API gave up waiting"),
    413: (RequestTooLargeError, "The request is larger than the API accepts"),
    422: (InvalidRequestError, "The API rejected the request as invalid"),
    429: (RateLimitError, "The API says there were too many requests"),
    500: (ServerUnavailableError, "The API couldn't handle the request"),
    502: (ServerUnavailableError, "The API couldn't handle the request"),
    503: (ServerUnavailableError, "The API couldn't handle the request"),
    504: (GroundlineTimeoutError, "The API ran out of time"),
}
_UNEXPECTED = "The API gave an answer the SDK doesn't recognise"
_SHORTEST_CREDENTIAL = 8


def _problem_text(problem: object) -> str | None:
    """One of a 422's problems as `location: message`, or None if it isn't one."""
    if not isinstance(problem, dict):
        return None
    location = problem.get("loc")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    message = problem.get("msg")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(location, list) or not isinstance(message, str):
        return None
    parts = cast(list[object], location)
    # The server's locations are field names and list positions. Anything else
    # is not the documented shape, and isn't copied into `detail`.
    if not all(
        isinstance(part, str) or (isinstance(part, int) and not isinstance(part, bool))
        for part in parts
    ):
        return None
    return f"{'.'.join(str(part) for part in parts)}: {message}"


def _detail_from_body(content: bytes) -> str | None:
    """The server's `detail`, if the body is the documented shape; else None.

    Documented shapes: `{"detail": "text"}` and a 422's list of problems.
    Anything else, such as a proxy's HTML page, gives None.
    """
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        problems = [_problem_text(problem) for problem in cast(list[object], detail)]
        if all(problem is not None for problem in problems):
            return "; ".join(problem for problem in problems if problem is not None)
    return None


def _without_credentials(text: str | None, credentials: Iterable[str]) -> str | None:
    """`text` with each credential, such as the API key a proxy echoed, replaced.

    A credential under `_SHORTEST_CREDENTIAL` characters isn't replaced: it would
    also match ordinary words.
    """
    if text is None:
        return None
    for credential in credentials:
        if len(credential) >= _SHORTEST_CREDENTIAL:
            text = text.replace(credential, "<redacted key>")
    return text


def error_for_status(
    status: int,
    headers: Mapping[str, str],
    content: bytes,
    credentials: Iterable[str] = (),
) -> GroundlineError:
    """The error for an answer with this status, headers and body.

    `credentials` are the texts to hide from `detail`, beyond the key shapes
    `GroundlineError` already redacts: the client's own API key, in case the
    server or a proxy echoes it. List the longest first.
    """
    error_class, start_of_message = _BY_STATUS.get(
        status, (GroundlineError, _UNEXPECTED)
    )
    message = f"{start_of_message} (status {status})."
    detail = _without_credentials(_detail_from_body(content), credentials)
    if error_class is RateLimitError:
        return RateLimitError(
            message,
            status_code=status,
            detail=detail,
            retry_after=retry_after_seconds(
                headers.get("retry-after"), now=time.time()
            ),
        )
    return error_class(message, status_code=status, detail=detail)


def error_for_failure(failure: BaseException) -> GroundlineError | None:
    """The error for a failure to get an answer, or None if it isn't one of
    `httpx2`'s, which is then not the SDK's to convert."""
    if isinstance(failure, httpx2.TimeoutException):
        return GroundlineTimeoutError("The request took longer than the time allowed.")
    if isinstance(failure, (httpx2.NetworkError, httpx2.RemoteProtocolError)):
        return ServerUnavailableError("The API couldn't be reached.")
    if isinstance(failure, httpx2.HTTPError):
        return GroundlineError("The request couldn't be completed.")
    return None
