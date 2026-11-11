"""Real Caddy/ACME lifecycle test, isolated in Docker; never contacts a public CA.

Run with scripts/https-smoke-test. Pebble validates TLS-ALPN challenges rather
than skipping validation. Only this harness trusts its generated test root.
"""

import ipaddress
import json
import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PEBBLE = (
    "ghcr.io/letsencrypt/pebble@sha256:"
    "0c81f05db371f436c97fb894bd6d2ab8fc9c8e2ce40f8bef8a8e15941bcd75ef"
)
PYTHON = (
    "python:3.13-slim-trixie@sha256:"
    "ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1"
)


def command(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def docker(*args: str) -> str:
    return command("docker", *args)


def main() -> None:
    name = "groundline-https-" + uuid.uuid4().hex[:10]
    containers: list[str] = []
    volume = name + "-data"
    env = dict(
        os.environ,
        POSTGRES_PASSWORD="test",
        GROUNDLINE_DB_PASSWORD="test",
        GROUNDLINE_PUBLIC_IP="203.0.113.10",
    )
    config = json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                "compose.yaml",
                "-f",
                "compose.https.yaml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
        )
    )
    for service in ("app", "postgres"):
        assert not config["services"][service].get("ports"), service
    caddy_image = config["services"]["caddy"]["image"]
    assert [p["published"] for p in config["services"]["caddy"]["ports"]] == ["443"]
    assert set(config["services"]["caddy"]["environment"]) == {"GROUNDLINE_PUBLIC_IP"}
    docker("network", "create", name)
    try:
        network = json.loads(docker("network", "inspect", name))[0]
        subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
        address = str(subnet.network_address + 10)
        with tempfile.TemporaryDirectory(prefix="groundline-https-") as directory:
            work = Path(directory)
            command(
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(work / "key.pem"),
                "-out",
                str(work / "cert.pem"),
                "-days",
                "1",
                "-subj",
                "/CN=pebble",
                "-addext",
                "subjectAltName=DNS:pebble",
            )
            (work / "key.pem").chmod(0o644)  # disposable test key, for Pebble's user
            pebble_config = {
                "pebble": {
                    "listenAddress": "0.0.0.0:14000",
                    "managementListenAddress": "0.0.0.0:15000",
                    "certificate": "/fixture/cert.pem",
                    "privateKey": "/fixture/key.pem",
                    "httpPort": 80,
                    "tlsPort": 443,
                    "profiles": {
                        "shortlived": {
                            "description": "test renewal",
                            "validityPeriod": 90,
                        }
                    },
                }
            }
            (work / "pebble.json").write_text(json.dumps(pebble_config))
            backend = """import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/slow': time.sleep(125)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *args): pass
ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
"""
            (work / "backend.py").write_text(backend)

            def start(
                suffix: str, image: str, *args: str, options: tuple[str, ...] = ()
            ) -> str:
                container = name + "-" + suffix
                containers.append(container)
                docker(
                    "run",
                    "-d",
                    "--name",
                    container,
                    "--network",
                    name,
                    "--network-alias",
                    suffix,
                    "-v",
                    f"{work}:/fixture:ro",
                    *options,
                    image,
                    *args,
                )
                return container

            app = start("app", PYTHON, "python", "/fixture/backend.py")
            ca = start(
                "pebble",
                PEBBLE,
                "-config",
                "/fixture/pebble.json",
                options=(
                    "-e",
                    "PEBBLE_VA_NOSLEEP=1",
                    "-e",
                    "PEBBLE_WFE_NONCEREJECT=0",
                    "-e",
                    "PEBBLE_AUTHZREUSE=0",
                ),
            )
            adapted: dict[str, Any] = json.loads(
                docker(
                    "run",
                    "--rm",
                    "-e",
                    f"GROUNDLINE_PUBLIC_IP={address}",
                    "-v",
                    f"{ROOT / 'infra/https/Caddyfile'}:/etc/caddy/Caddyfile:ro",
                    caddy_image,
                    "caddy",
                    "adapt",
                    "--config",
                    "/etc/caddy/Caddyfile",
                )
            )
            tls = adapted["apps"]["tls"]["automation"]
            issuer = tls["policies"][0]["issuers"][0]
            assert issuer["module"] == "acme" and issuer["profile"] == "shortlived"
            assert issuer["ca"] == "https://acme-v02.api.letsencrypt.org/directory"
            assert issuer["challenges"]["http"]["disabled"] is True
            # The test alone changes the CA/trust and renewal scan frequency.
            issuer.update(
                ca="https://pebble:14000/dir",
                test_ca="https://pebble:14000/dir",
                trusted_roots_pem_files=["/fixture/cert.pem"],
            )
            tls["renew_interval"] = 1_000_000_000
            (work / "caddy.json").write_text(json.dumps(adapted))
            proxy = start(
                "caddy",
                caddy_image,
                "caddy",
                "run",
                "--config",
                "/fixture/caddy.json",
                options=("--ip", address, "-v", f"{volume}:/data"),
            )

            def client(source: str) -> str:
                return docker("exec", app, "python", "-c", source)

            # Trust the issuing root from the test CA's authenticated management API.
            fetch_root = """import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/fixture/cert.pem')
root = urllib.request.urlopen('https://pebble:15000/roots/0', context=ctx).read()
open('/tmp/issuer.pem', 'wb').write(root)
"""
            wait_for(lambda: client(fetch_root), "test CA readiness")
            connect = f"""import ssl, socket, hashlib
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
with socket.create_connection(('{address}', 443), timeout=150) as tcp:
    with ctx.wrap_socket(tcp, server_hostname='{address}') as tls:
        print(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())
"""
            first = wait_for(
                lambda: client(connect), "IP certificate issuance/no-SNI handshake"
            )
            print(
                "PASS: real TLS-ALPN issuance and IP identity verification without SNI",
                flush=True,
            )
            for old, new in [
                ("cafile='/tmp/issuer.pem'", ""),
                (f"server_hostname='{address}'", "server_hostname='192.0.2.55'"),
            ]:
                try:
                    client(connect.replace(old, new))
                except RuntimeError as error:
                    assert "CERTIFICATE_VERIFY_FAILED" in str(error), error
                else:
                    raise AssertionError(
                        "untrusted or wrong-identity certificate accepted"
                    )
            print(
                "PASS: untrusted certificate and incorrect identity rejected",
                flush=True,
            )
            docker("restart", proxy)
            assert wait_for(lambda: client(connect), "restart recovery") == first
            print("PASS: certificate retained across container restart", flush=True)

            def renewed() -> str:
                fingerprint = client(connect)
                if fingerprint == first:
                    raise RuntimeError("waiting for a different served certificate")
                return fingerprint

            wait_for(renewed, "automatic renewal and loading", seconds=180)
            print(
                "PASS: Caddy renewed and served a new certificate automatically",
                flush=True,
            )
            client(f"""import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
response = urllib.request.urlopen('https://{address}/slow', context=ctx, timeout=150)
assert response.read() == b'ok'
""")
            print("PASS: response after 125 seconds of silence", flush=True)
            # A failed renewal must not expose HTTP or mint a locally trusted fallback.
            docker("stop", ca)
            time.sleep(95)
            try:
                client(connect)
            except RuntimeError as error:
                assert "CERTIFICATE_VERIFY_FAILED" in str(error), error
            else:
                raise AssertionError("expired certificate accepted")
            client(f"""import socket
for port in (80, 8000, 8001, 5432, 5433, 2019):
    with socket.socket() as connection:
        connection.settimeout(2)
        assert connection.connect_ex(('{address}', port)) != 0, port
""")
            docker("start", ca)
            wait_for(lambda: client(connect), "recovery after CA outage", seconds=180)
            print(
                "PASS: expiry fails closed; renewal recovers after CA outage",
                flush=True,
            )
    except BaseException:
        for container in containers:
            print(command("docker", "logs", "--tail", "40", container, check=False))
        raise
    finally:
        for container in reversed(containers):
            command("docker", "rm", "-f", container, check=False)
        command("docker", "volume", "rm", volume, check=False)
        command("docker", "network", "rm", name, check=False)


def wait_for(operation: Callable[[], str], description: str, seconds: int = 90) -> str:
    deadline = time.monotonic() + seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return operation()
        except RuntimeError as error:
            last_error = str(error)
            time.sleep(2)
    raise RuntimeError(f"Timed out: {description}\n{last_error}")


if __name__ == "__main__":
    main()
