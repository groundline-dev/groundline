"""The errors the SDK raises.

`GroundlineError` is the base: catch it to handle everything the SDK raises on
purpose. The subclasses say what the caller can do: fix the key
(`AuthenticationError`), wait (`RateLimitError`), fix the request
(`InvalidRequestError`, `RequestTooLargeError`), or try again later
(`ServerUnavailableError`, `GroundlineTimeoutError`). A plain `GroundlineError`
means an answer the SDK doesn't recognise. `GroundlineConfigError` is raised
when a client is created with a missing or unusable setting.

Every message is fixed wording plus a status code, so it is safe to log or show
to anyone: it never holds the source, the summary, the API key or the server's
own text. That text is in `detail`, with anything shaped like an API key
replaced by `<redacted key>`. Only the two key shapes below are recognised.

An error never chains the `httpx2` error it replaces, and has no request or
response attribute. The `httpx2` error carries its request, and the request's
headers hold the API key. The frames of the error's own traceback can still
reach the request, so a tool that prints a frame's local variables relies on
`repr`, which never shows the headers.
"""

import re

# The same two shapes as `redact_keys` in groundline_core/_core/errors.py: an
# Anthropic API key, and a groundline API key (`gl_` plus 43 characters, see
# api_keys.py in groundline_core). The SDK can't import groundline_core (ADR
# 0019), so keep the two in step by hand; a test builds a key the server's way.
KEY_SHAPES = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|gl_[A-Za-z0-9_-]{20,}")

DETAIL_LIMIT = 250
"""The most characters `detail` holds, counting the ellipsis that marks a cut."""


def redact_keys(text: str) -> str:
    """`text` with anything shaped like an API key replaced."""
    return KEY_SHAPES.sub("<redacted key>", text)


def _safe_detail(detail: str) -> str:
    # Redact first: cutting first could leave the start of a key, too short to
    # match, in the text.
    detail = redact_keys(detail)
    if len(detail) > DETAIL_LIMIT:
        return detail[: DETAIL_LIMIT - 1] + "…"
    return detail


class GroundlineError(Exception):
    """The base of every error the SDK raises on purpose.

    Raised by itself for an answer the SDK doesn't recognise: a redirect, a
    status the API doesn't document, or a reply it can't read.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        # Redacted here, so `str`, `repr` and `args` are all clean.
        super().__init__(redact_keys(message))
        self.status_code = status_code
        """The HTTP status of the answer, or None when there was no answer."""
        self.detail = None if detail is None else _safe_detail(detail)
        """The server's own explanation, when it sent one in the documented
        shape, redacted and cut to 250 characters; otherwise None."""


class AuthenticationError(GroundlineError):
    """The API refused the API key (a 401 or 403). Retrying won't help until
    the key is fixed."""


class RateLimitError(GroundlineError):
    """The API (or a proxy in front of it) said there were too many requests,
    after the SDK's retries (a 429)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, detail=detail)
        self.retry_after = retry_after
        """The seconds the last answer's `Retry-After` header asked you to wait,
        or None when it was missing or unreadable."""


class InvalidRequestError(GroundlineError):
    """The API rejected the request as invalid (a 400 or 422). Fix the request;
    `detail` says where."""


class RequestTooLargeError(GroundlineError):
    """The request was larger than the API accepts (a 413). Send less text."""


class ServerUnavailableError(GroundlineError):
    """The API couldn't answer (a 500, 502 or 503), or couldn't be reached.
    Trying again later may work."""


class GroundlineTimeoutError(GroundlineError):
    """The call ran out of time: the API said its time limit expired (a 408 or
    504), or the SDK's own limit did. A retry may repeat paid model work."""


class GroundlineConfigError(GroundlineError, ValueError):
    """The URL, API key or an option needed to create a client is missing or
    unusable.

    The message says which setting is wrong and how to set it. It never
    contains the value, because the value may be the API key.
    """
