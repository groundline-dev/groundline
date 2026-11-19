"""The SDK's error classes and the rules that turn an answer into one (ALL-158).

These tests build errors and call the mapping directly, with no server. The
client tests in `test_client_errors.py` show the same rules through a call.
"""

import json
import secrets

import groundline_client as sdk
import httpx2
import pytest
from groundline_client._error_mapping import (  # pyright: ignore[reportPrivateUsage]
    error_for_failure,
    error_for_status,
)

SUBCLASSES = [
    sdk.AuthenticationError,
    sdk.RateLimitError,
    sdk.InvalidRequestError,
    sdk.RequestTooLargeError,
    sdk.ServerUnavailableError,
    sdk.GroundlineTimeoutError,
]
TRANSPORT_TEXT = "transport-detail-xyz"
SECRET_SHAPES = {
    "an Anthropic key": "sk-ant-api03-" + "A1b2C3d4" * 6,
    # Built the way the server builds a groundline API key (api_keys.py).
    "a groundline key": "gl_" + secrets.token_urlsafe(32),
}


# R1 (error classes)


@pytest.mark.parametrize("error_class", SUBCLASSES, ids=lambda c: c.__name__)
def test_each_error_is_a_groundline_error(error_class: type[sdk.GroundlineError]):
    assert issubclass(error_class, sdk.GroundlineError)
    assert issubclass(sdk.GroundlineError, Exception)


def test_the_config_error_is_a_groundline_error_and_still_a_value_error():
    assert issubclass(sdk.GroundlineConfigError, sdk.GroundlineError)
    assert issubclass(sdk.GroundlineConfigError, ValueError)


def test_a_rate_limit_error_carries_the_wait_time():
    error = sdk.RateLimitError("slow down", status_code=429, retry_after=7.5)
    assert error.retry_after == 7.5
    assert error.status_code == 429


def test_a_plain_error_has_no_status_or_detail_unless_given():
    error = sdk.GroundlineError("unexpected")
    assert error.status_code is None
    assert error.detail is None


# R5 (no secrets in messages): redaction and the cap


@pytest.mark.parametrize("key", SECRET_SHAPES.values(), ids=SECRET_SHAPES.keys())
def test_a_key_shaped_text_is_redacted_everywhere_the_error_shows_it(key: str):
    error = sdk.GroundlineError(f"bad {key} here", detail=f"also {key}")

    assert "<redacted key>" in str(error)
    assert key not in str(error)
    assert key not in repr(error)
    assert key not in repr(error.args)
    assert error.detail is not None
    assert key not in error.detail


def test_the_config_error_redacts_a_key_too():
    key = SECRET_SHAPES["a groundline key"]
    assert key not in str(sdk.GroundlineConfigError(f"the URL is {key}"))


def test_ordinary_text_is_left_alone():
    error = sdk.GroundlineError("the API answered 502", detail="stage 'extract' failed")
    assert str(error) == "the API answered 502"
    assert error.detail == "stage 'extract' failed"


def test_a_detail_of_250_characters_is_kept_whole():
    detail = "x" * 250
    assert sdk.GroundlineError("m", detail=detail).detail == detail


def test_a_longer_detail_is_cut_to_250_characters_ending_in_an_ellipsis():
    error = sdk.GroundlineError("m", detail="x" * 251)
    assert error.detail == "x" * 249 + "…"


def test_a_key_that_straddles_the_cut_is_still_hidden():
    key = SECRET_SHAPES["a groundline key"]
    # Redact first, then cut: cutting first could leave a key's start, too short
    # to match, in the text.
    error = sdk.GroundlineError("m", detail="y" * 230 + key)
    assert error.detail is not None
    assert key[:20] not in error.detail


# R2 (status maps to subclass), R4 (malformed responses): the mapping alone


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, sdk.InvalidRequestError),
        (401, sdk.AuthenticationError),
        (403, sdk.AuthenticationError),
        (404, sdk.GroundlineError),
        (408, sdk.GroundlineTimeoutError),
        (413, sdk.RequestTooLargeError),
        (418, sdk.GroundlineError),
        (422, sdk.InvalidRequestError),
        (429, sdk.RateLimitError),
        (500, sdk.ServerUnavailableError),
        (502, sdk.ServerUnavailableError),
        (503, sdk.ServerUnavailableError),
        (504, sdk.GroundlineTimeoutError),
        (302, sdk.GroundlineError),
    ],
)
def test_each_status_maps_to_exactly_one_class(
    status: int, expected: type[sdk.GroundlineError]
):
    error = error_for_status(status, {}, b'{"detail": "x"}')

    assert type(error) is expected
    assert error.status_code == status
    assert str(status) in str(error)


def test_the_message_never_holds_the_servers_detail():
    error = error_for_status(502, {}, b'{"detail": "SOURCE-TEXT-123"}')

    assert error.detail == "SOURCE-TEXT-123"
    assert "SOURCE-TEXT-123" not in str(error)


def test_a_422s_problems_are_listed_as_location_and_message():
    body = (
        b'{"detail": [{"loc": ["body", "source"], "msg": "Field required",'
        b' "type": "missing"}, {"loc": ["body", "summary"],'
        b' "msg": "Input should be a valid string", "type": "string_type"}]}'
    )

    error = error_for_status(422, {}, body)

    assert error.detail == (
        "body.source: Field required; body.summary: Input should be a valid string"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html><body>502 Bad Gateway</body></html>",
        b"{not json",
        b"[1, 2]",
        b"null",
        b'{"other": "field"}',
        b'{"detail": 5}',
        b'{"detail": [1, 2]}',
        b'{"detail": [{"loc": "x"}]}',
        b"\xff\xfe not utf-8",
    ],
    ids=[
        "empty",
        "html",
        "broken-json",
        "list",
        "null",
        "no-detail",
        "number-detail",
        "list-of-numbers",
        "problem-without-message",
        "not-utf-8",
    ],
)
def test_a_body_that_is_not_the_documented_shape_gives_the_class_with_no_detail(
    body: bytes,
):
    error = error_for_status(502, {}, body)

    assert type(error) is sdk.ServerUnavailableError
    assert error.detail is None
    assert str(error)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("0", 0.0), ("2.5", 2.5), (None, None), ("soon", None), ("-3", None)],
)
def test_a_rate_limit_reads_retry_after_in_seconds(
    header: str | None, expected: float | None
):
    headers = {} if header is None else {"retry-after": header}

    error = error_for_status(429, headers, b"{}")

    assert isinstance(error, sdk.RateLimitError)
    assert error.retry_after == expected


def test_a_retry_after_date_in_the_past_means_no_wait():
    headers = {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}

    error = error_for_status(429, headers, b"{}")

    assert isinstance(error, sdk.RateLimitError)
    assert error.retry_after == 0.0


@pytest.mark.parametrize(
    "location",
    [[{"a": 1}], [["nested"]], [None], [1.5], [True, "x"]],
    ids=["dict", "list", "none", "float", "bool"],
)
def test_a_422_location_that_is_not_names_and_positions_gives_no_detail(
    location: list[object],
):
    body = json.dumps({"detail": [{"loc": location, "msg": "m", "type": "t"}]})

    error = error_for_status(422, {}, body.encode())

    assert type(error) is sdk.InvalidRequestError
    assert error.detail is None


def test_a_credential_in_the_servers_detail_is_replaced():
    body = b'{"detail": "echo Bearer abcdef123456 and abcdef123456"}'

    error = error_for_status(502, {}, body, "abcdef123456")

    assert error.detail == "echo Bearer <redacted key> and <redacted key>"


def test_a_credential_that_contains_spaces_is_replaced_whole():
    body = b'{"detail": "echo my secret key phrase done"}'

    error = error_for_status(502, {}, body, "my secret key phrase")

    assert error.detail == "echo <redacted key> done"


def test_a_credential_too_short_to_be_a_key_is_left_alone():
    error = error_for_status(502, {}, b'{"detail": "a key x here"}', "x")
    assert error.detail == "a key x here"


def test_only_a_rate_limit_error_has_a_wait_time():
    error = error_for_status(503, {"retry-after": "7"}, b"{}")
    assert not hasattr(error, "retry_after")


# A1: failures that are not an answer


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx2.ReadTimeout(TRANSPORT_TEXT), sdk.GroundlineTimeoutError),
        (httpx2.ConnectTimeout(TRANSPORT_TEXT), sdk.GroundlineTimeoutError),
        (httpx2.ConnectError(TRANSPORT_TEXT), sdk.ServerUnavailableError),
        (httpx2.ReadError(TRANSPORT_TEXT), sdk.ServerUnavailableError),
        (httpx2.RemoteProtocolError(TRANSPORT_TEXT), sdk.ServerUnavailableError),
        (httpx2.UnsupportedProtocol(TRANSPORT_TEXT), sdk.GroundlineError),
        (httpx2.ProxyError(TRANSPORT_TEXT), sdk.GroundlineError),
    ],
    ids=lambda value: type(value).__name__,
)
def test_a_transport_failure_maps_to_one_class_with_no_status(
    failure: Exception, expected: type[sdk.GroundlineError]
):
    error = error_for_failure(failure)

    assert error is not None
    assert type(error) is expected
    assert error.status_code is None
    assert error.detail is None
    assert TRANSPORT_TEXT not in str(error)


def test_an_error_that_is_not_from_httpx_is_not_converted():
    assert error_for_failure(RuntimeError("a bug")) is None
    assert error_for_failure(KeyboardInterrupt()) is None
