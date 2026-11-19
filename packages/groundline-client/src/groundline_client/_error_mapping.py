"""Turn an answer, or a failure to get one, into the SDK's error.

Plain functions that take no request or response, only the status, the headers
and the body bytes, so the error they build can't hold the API key (the
request's headers do). The classes are in `errors.py`; `_http.py` calls these.
"""

import json
import time
from collections.abc import Mapping
from typing import Any, cast

import httpx2

from ._generated.models import ErrorBody, InvalidRequestBody
from ._retry import retry_after_of
from .errors import (
    AuthenticationError,
    GroundlineError,
    GroundlineTimeoutError,
    InvalidRequestError,
    RateLimitError,
    RequestTooLargeError,
    ServerUnavailableError,
)

_INVALID = (InvalidRequestError, "The API rejected the request as invalid")
_REFUSED_KEY = (AuthenticationError, "The API refused the API key")
# The API never answers 403. It comes from a proxy or firewall in front of it,
# often for a reason that has nothing to do with the key.
_REFUSED_REQUEST = (AuthenticationError, "The API refused the request")
_GAVE_UP = (GroundlineTimeoutError, "The API gave up waiting")
_OUT_OF_TIME = (GroundlineTimeoutError, "The API ran out of time")
_TOO_LARGE = (RequestTooLargeError, "The request is larger than the API accepts")
_TOO_MANY = (RateLimitError, "The API says there were too many requests")
_FAILED = (ServerUnavailableError, "The API couldn't handle the request")
_UNEXPECTED = (GroundlineError, "The API gave an answer the SDK doesn't recognize")

# The statuses the API sends (400, 401, 413, 422, 500, 502, 503, 504) and the
# ones a proxy in front of it adds (403, 408, 429).
_BY_STATUS: dict[int, tuple[type[GroundlineError], str]] = {
    400: _INVALID,
    401: _REFUSED_KEY,
    403: _REFUSED_REQUEST,
    408: _GAVE_UP,
    413: _TOO_LARGE,
    422: _INVALID,
    429: _TOO_MANY,
    500: _FAILED,
    502: _FAILED,
    503: _FAILED,
    504: _OUT_OF_TIME,
}
_SHORTEST_CREDENTIAL = 8


def _is_location(location: object) -> bool:
    """Whether a 422 problem's location is a list of field names and positions."""
    return isinstance(location, list) and all(
        isinstance(part, str) or (isinstance(part, int) and not isinstance(part, bool))
        for part in cast(list[object], location)
    )


def _text_detail(body: dict[str, Any]) -> str | None:
    """The `detail` of an `ErrorBody` (every error but a 422), if it is text."""
    try:
        error_body = ErrorBody.from_dict(body)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    detail = cast(object, error_body.detail)
    return detail if isinstance(detail, str) else None


def _problems_detail(body: dict[str, Any]) -> str | None:
    """A 422's problems as `location: message; ...`, if they are all well formed."""
    items = body.get("detail")
    if not isinstance(items, list) or not items:
        return None
    # The generated model would read a string location as a list of characters.
    if not all(
        isinstance(item, dict)
        and isinstance(cast(dict[str, Any], item).get("loc"), list)
        for item in cast(list[object], items)
    ):
        return None
    try:
        invalid = InvalidRequestBody.from_dict(body)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    texts: list[str] = []
    for problem in invalid.detail:
        location = cast(object, problem.loc)
        message = cast(object, problem.msg)
        if not isinstance(message, str) or not _is_location(location):
            return None
        parts = cast(list[object], location)
        texts.append(f"{'.'.join(str(part) for part in parts)}: {message}")
    return "; ".join(texts)


def _detail_from_body(content: bytes) -> str | None:
    """The server's `detail`, if the body is one of the documented shapes.

    The shapes are the generated `ErrorBody` and `InvalidRequestBody`. Anything
    else, such as a proxy's HTML page, gives None.
    """
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict):
        return None
    fields = cast(dict[str, Any], body)
    detail = _text_detail(fields)
    return detail if detail is not None else _problems_detail(fields)


def _without_credential(text: str | None, credential: str) -> str | None:
    """`text` with the credential, such as the API key a proxy echoed, replaced.

    A credential under `_SHORTEST_CREDENTIAL` characters isn't replaced: it would
    also match ordinary words.
    """
    if text is None or len(credential) < _SHORTEST_CREDENTIAL:
        return text
    return text.replace(credential, "<redacted key>")


def error_for_status(
    status: int, headers: Mapping[str, str], content: bytes, credential: str = ""
) -> GroundlineError:
    """The error for an answer with this status, headers and body.

    `credential` is the client's own API key. It is hidden from `detail`, beyond
    the key shapes `GroundlineError` already redacts, in case the server or a
    proxy echoes it.
    """
    error_class, start_of_message = _BY_STATUS.get(status, _UNEXPECTED)
    extra: dict[str, Any] = {}
    if error_class is RateLimitError:
        extra["retry_after"] = retry_after_of(headers, now=time.time())
    return error_class(
        f"{start_of_message} (status {status}).",
        status_code=status,
        detail=_without_credential(_detail_from_body(content), credential),
        **extra,
    )


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
