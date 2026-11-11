"""Check external probe failures cannot be mistaken for isolation."""

import errno
from unittest.mock import MagicMock, patch

import pytest

from scripts.https_check import check, endpoint


@pytest.mark.parametrize(
    "url,ip",
    [
        ("http://1.1.1.1", "1.1.1.1"),
        ("https://1.1.1.1:443", "1.1.1.1"),
        ("https://1.1.1.1/", "1.1.1.1"),
        ("https://2.2.2.2", "1.1.1.1"),
        ("https://127.0.0.1", "127.0.0.1"),
        ("https://example.com", "1.1.1.1"),
    ],
)
def test_exact_public_ipv4_endpoint_required(url: str, ip: str):
    with pytest.raises(ValueError):
        endpoint(url, ip)


def test_public_endpoint():
    assert endpoint("https://1.1.1.1", "1.1.1.1") == "1.1.1.1"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionRefusedError(),
        OSError(errno.EHOSTUNREACH, "unreachable"),
    ],
)
def test_failed_positive_handshake_is_never_isolation_success(error: OSError):
    with patch(
        "scripts.https_check.socket.create_connection", side_effect=error
    ) as connect:
        with pytest.raises(OSError):
            check("1.1.1.1")
        assert connect.call_count == 1


@pytest.mark.parametrize(
    "failure", [None, OSError(errno.EHOSTUNREACH, "routing failure"), TimeoutError()]
)
def test_probe_distinguishes_open_ports_network_errors_and_filtering(
    failure: OSError | None,
):
    connection = MagicMock()
    tls = MagicMock()
    tls.getpeercert.side_effect = [{"notAfter": "tomorrow"}, b"certificate"]
    context = MagicMock()
    context.wrap_socket.return_value.__enter__.return_value = tls
    responses = [connection] + ([failure] * 6 if failure else [connection])
    with (
        patch("scripts.https_check.ssl.create_default_context", return_value=context),
        patch("scripts.https_check.socket.create_connection", side_effect=responses),
    ):
        if isinstance(failure, TimeoutError):
            check("1.1.1.1")
        else:
            with pytest.raises((RuntimeError, OSError)):
                check("1.1.1.1")
        assert context.wrap_socket.call_args.kwargs["server_hostname"] == "1.1.1.1"
