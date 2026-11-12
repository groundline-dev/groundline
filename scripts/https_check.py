"""Verify an AWS IP endpoint using system trust, without SNI, then probe isolation.

Used by aws-smoke-test; has no app dependencies and never sends an API key.
"""

import argparse
import contextlib
import errno
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def endpoint(url: str, public_ip: str) -> str:
    address = ipaddress.IPv4Address(public_ip)
    if not address.is_global:
        raise ValueError("HTTPS requires the instance's public IPv4 address")
    if url != f"https://{address}":
        raise ValueError("HTTPS URL must be https://<public_ip>, without redirects")
    return str(address)


def check(address: str) -> dict[str, Any]:
    # Don't implicitly trust a development CA through environment overrides.
    for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        os.environ.pop(variable, None)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Python omits SNI for an IP literal while still checking the IP SAN.
    with socket.create_connection((address, 443), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=address) as tls:
            certificate = tls.getpeercert()
            der = tls.getpeercert(binary_form=True)
            assert certificate and der
            evidence = {
                "public_ip": address,
                "sha256": hashlib.sha256(der).hexdigest(),
                "issuer": certificate["issuer"],
                "not_before": certificate["notBefore"],
                "not_after": certificate["notAfter"],
            }
            print(
                f"Trusted IP certificate without SNI; expires {certificate['notAfter']}"
            )
            print(f"SHA256: {evidence['sha256']}")
    # A positive trusted handshake precedes these probes: an unreachable host
    # must not turn every negative connection test into a false pass.
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(
            pool.map(
                lambda port: check_port(address, port),
                (80, 8000, 8001, 5432, 5433, 2019),
            )
        )
    print("Direct HTTP, app, PostgreSQL and Caddy admin ports are closed or filtered")
    check_redirect(address, context)
    evidence["checks"] = {
        "public_trust": True,
        "ip_identity": True,
        "no_sni": True,
        "port_isolation": True,
        "https_redirect": True,
    }
    return evidence


def check_port(address: str, port: int) -> None:
    try:
        with socket.create_connection((address, port), timeout=3):
            raise RuntimeError(f"Direct TCP port {port} is reachable")
    except (TimeoutError, ConnectionRefusedError):
        return
    except OSError as error:
        if error.errno not in (errno.ETIMEDOUT, errno.ECONNREFUSED):
            raise


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


def access_policy(mode: str, allowed_cidr: str) -> str:
    network = ipaddress.IPv4Network(allowed_cidr)
    if (mode == "restricted" and network.prefixlen == 32) or (
        mode == "unrestricted" and allowed_cidr == "0.0.0.0/0"
    ):
        return str(network.network_address)
    raise ValueError("access mode and allowed CIDR must describe the same policy")


def check_source_filter(address: str, mode: str, allowed_cidr: str) -> None:
    """Run inside the app container: its real peer IP is outside the tester /32.

    Connect to Caddy directly but verify its public IP certificate with ordinary
    system trust. Spoof the allowed source in headers to test the live filter.
    """
    claimed_source = access_policy(mode, allowed_cidr)
    proxy = str(ipaddress.IPv4Address(os.environ.get("FORWARDED_ALLOW_IPS", "")))
    for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        os.environ.pop(variable, None)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((proxy, 443), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=address) as tls:
            tls.sendall(
                (
                    f"GET /health HTTP/1.1\r\nHost: {address}\r\n"
                    f"X-Forwarded-For: {claimed_source}\r\n"
                    f"X-Real-IP: {claimed_source}\r\n"
                    f"Forwarded: for={claimed_source}\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = http.client.HTTPResponse(tls)
            try:
                response.begin()
                expected = 403 if mode == "restricted" else 200
                if response.status != expected:
                    raise RuntimeError(
                        f"Caddy source-IP filter returned {response.status}, "
                        f"expected {expected} for {mode} access"
                    )
            finally:
                response.close()
    print(f"Live {mode} source-IP policy verified with spoofed forwarded headers")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("public_ip")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--source-filter", choices=("restricted", "unrestricted"))
    parser.add_argument("--allowed-cidr", default="")
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON evidence on stdout; progress on stderr",
    )
    args = parser.parse_args()
    try:
        address = endpoint(args.url, args.public_ip)
        if args.source_filter:
            access_policy(args.source_filter, args.allowed_cidr)
        if not args.validate_only:
            if args.source_filter:
                check_source_filter(address, args.source_filter, args.allowed_cidr)
            elif args.json:
                with contextlib.redirect_stdout(sys.stderr):
                    evidence = check(address)
                print(json.dumps(evidence))
            else:
                check(address)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"HTTPS check failed: {error}\n")


if __name__ == "__main__":
    main()
