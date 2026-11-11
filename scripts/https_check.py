"""Verify an AWS IP endpoint using system trust, without SNI, then probe isolation.

Used by aws-smoke-test; has no app dependencies and never sends an API key.
"""

import argparse
import errno
import hashlib
import http.client
import ipaddress
import os
import socket
import ssl
from urllib.parse import urlsplit


def endpoint(url: str, public_ip: str) -> str:
    address = ipaddress.IPv4Address(public_ip)
    if not address.is_global:
        raise ValueError("HTTPS requires the instance's public IPv4 address")
    parsed = urlsplit(url)
    if url != f"https://{address}" or parsed.hostname != str(address):
        raise ValueError("HTTPS URL must be https://<public_ip>, without redirects")
    return str(address)


def check(address: str) -> None:
    # Don't implicitly trust a development CA through environment overrides.
    for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        os.environ.pop(variable, None)
    context = ssl.create_default_context()
    # Python omits SNI for an IP literal while still checking the IP SAN.
    with socket.create_connection((address, 443), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=address) as tls:
            certificate = tls.getpeercert()
            der = tls.getpeercert(binary_form=True)
            assert certificate and der
            print(
                f"Trusted IP certificate without SNI; expires {certificate['notAfter']}"
            )
            print(f"SHA256: {hashlib.sha256(der).hexdigest()}")
    # A positive trusted handshake precedes these probes: an unreachable host
    # must not turn every negative connection test into a false pass.
    for port in (80, 8000, 8001, 5432, 5433, 2019):
        try:
            with socket.create_connection((address, port), timeout=3):
                raise RuntimeError(f"Direct TCP port {port} is reachable")
        except (TimeoutError, ConnectionRefusedError):
            continue
        except OSError as error:
            if error.errno not in (errno.ETIMEDOUT, errno.ECONNREFUSED):
                raise
    print("Direct HTTP, app, PostgreSQL and Caddy admin ports are closed or filtered")
    check_redirect(address, context)


def check_redirect(address: str, context: ssl.SSLContext) -> None:
    # Do not follow redirects: a lost forwarded scheme must fail rather than
    # silently sending the next request in plaintext. Caddy must also replace
    # a caller's spoofed X-Forwarded-Proto before the app trusts it.
    connection = http.client.HTTPSConnection(address, timeout=10, context=context)
    try:
        connection.request("GET", "/docs/", headers={"X-Forwarded-Proto": "http"})
        response = connection.getresponse()
        if (
            response.status != 307
            or response.getheader("Location") != f"https://{address}/docs"
        ):
            raise RuntimeError("The proxy must keep application redirects on HTTPS")
    finally:
        connection.close()
    print(
        "Application redirects preserve HTTPS, including with spoofed forwarded headers"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("public_ip")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        address = endpoint(args.url, args.public_ip)
        if not args.validate_only:
            check(address)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"HTTPS check failed: {error}\n")


if __name__ == "__main__":
    main()
