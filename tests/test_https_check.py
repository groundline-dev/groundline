"""Check external probe failures cannot be mistaken for isolation."""

import errno
import ssl
from unittest.mock import MagicMock, patch

import pytest

from scripts.https_check import check, check_redirect, endpoint


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
    tls.getpeercert.side_effect = [
        {"notAfter": "tomorrow", "notBefore": "today", "issuer": "test"},
        b"certificate",
    ]
    context = MagicMock()
    context.minimum_version = None

    def wrap_socket(*args: object, **kwargs: object):
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        return tls

    tls.__enter__.return_value = tls
    context.wrap_socket.side_effect = wrap_socket
    responses = [connection] + ([failure] * 6 if failure else [connection] * 6)
    with (
        patch("scripts.https_check.ssl.create_default_context", return_value=context),
        patch("scripts.https_check.socket.create_connection", side_effect=responses),
        patch("scripts.https_check.check_redirect") as redirect,
    ):
        if isinstance(failure, TimeoutError):
            check("1.1.1.1")
            redirect.assert_called_once_with("1.1.1.1", context)
        else:
            with pytest.raises((RuntimeError, OSError)):
                check("1.1.1.1")
        assert context.wrap_socket.call_args.kwargs["server_hostname"] == "1.1.1.1"


@pytest.mark.parametrize(
    "status,location,valid",
    [
        (307, "https://1.1.1.1/docs", True),
        (307, "http://1.1.1.1/docs", False),
        (307, "https://other.example/docs", False),
        (200, None, False),
    ],
)
def test_redirects_cannot_downgrade_or_change_host(
    status: int, location: str | None, valid: bool
):
    with patch("scripts.https_check.http.client.HTTPSConnection") as connect:
        connection = connect.return_value
        response = connection.getresponse.return_value
        response.status = status
        response.getheader.return_value = location
        if valid:
            check_redirect("1.1.1.1", ssl.create_default_context())
        else:
            with pytest.raises(RuntimeError, match="redirects on HTTPS"):
                check_redirect("1.1.1.1", ssl.create_default_context())
        connection.request.assert_called_once_with(
            "GET", "/docs/", headers={"X-Forwarded-Proto": "http"}
        )
        connection.close.assert_called_once()


def test_json_mode_keeps_progress_out_of_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    import json

    from scripts import https_check

    def fake_check(address: str) -> dict[str, object]:
        print("verified TLS")
        return {
            "public_ip": address,
            "sha256": "fingerprint",
            "checks": {"public_trust": True},
        }

    monkeypatch.setattr(https_check, "check", fake_check)
    monkeypatch.setattr(
        "sys.argv", ["https_check", "https://1.1.1.1", "1.1.1.1", "--json"]
    )
    https_check.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["sha256"] == "fingerprint"
    assert captured.err == "verified TLS\n"


def test_isolation_probes_run_concurrently():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from scripts.https_check import check_port

    barrier = threading.Barrier(6, timeout=5)

    def blocked(*args: object, **kwargs: object):
        barrier.wait()
        raise TimeoutError()

    def probe(port: int) -> None:
        check_port("1.1.1.1", port)

    with patch("scripts.https_check.socket.create_connection", side_effect=blocked):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(probe, range(6)))


def test_shell_formats_python_output_without_hiding_failures():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # Exercise the actual CLI entrypoint and the same shell formatter as aws-smoke.
    source = (
        "from scripts import https_check as h; "
        "h.check=lambda address: print('trusted certificate'); h.main()"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; source scripts/aws-common.sh; "
            'output="$("$1" -c "$2" https://1.1.1.1 1.1.1.1)"; '
            'check_results <<<"$output"',
            "test",
            sys.executable,
            source,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "  ok: trusted certificate\n"
    failure = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; source scripts/aws-common.sh; "
            'output="$("$1" -m scripts.https_check http://1.1.1.1 1.1.1.1)"; '
            'check_results <<<"$output"',
            "test",
            sys.executable,
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert failure.returncode != 0
    assert "ok:" not in failure.stdout


@pytest.mark.parametrize(
    "mode,cidr",
    [
        ("restricted", "0.0.0.0/0"),
        ("restricted", "203.0.113.0/24"),
        ("unrestricted", "203.0.113.7/32"),
        ("unknown", "0.0.0.0/0"),
    ],
)
def test_access_policy_rejects_inconsistent_modes(mode: str, cidr: str):
    from scripts.https_check import access_policy

    with pytest.raises(ValueError):
        access_policy(mode, cidr)


@pytest.mark.parametrize(
    "mode,cidr,status,valid",
    [
        ("restricted", "203.0.113.7/32", 403, True),
        ("restricted", "203.0.113.7/32", 200, False),
        ("unrestricted", "0.0.0.0/0", 200, True),
        ("unrestricted", "0.0.0.0/0", 403, False),
    ],
)
def test_live_source_policy_checks_the_response_and_public_identity(
    mode: str, cidr: str, status: int, valid: bool
):
    from scripts.https_check import check_source_filter

    tls = MagicMock()
    tls.__enter__.return_value = tls
    context = MagicMock()
    context.wrap_socket.return_value = tls
    with (
        patch.dict("os.environ", {"FORWARDED_ALLOW_IPS": "172.30.254.2"}),
        patch("scripts.https_check.ssl.create_default_context", return_value=context),
        patch("scripts.https_check.socket.create_connection") as connect,
        patch("scripts.https_check.http.client.HTTPResponse") as response,
    ):
        response.return_value.status = status
        if valid:
            check_source_filter("1.1.1.1", mode, cidr)
        else:
            with pytest.raises(RuntimeError, match="source-IP filter"):
                check_source_filter("1.1.1.1", mode, cidr)
        connect.assert_called_once_with(("172.30.254.2", 443), timeout=10)
        assert context.wrap_socket.call_args.kwargs["server_hostname"] == "1.1.1.1"
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        assert b"X-Forwarded-For:" in tls.sendall.call_args.args[0]
        response.return_value.close.assert_called_once()
