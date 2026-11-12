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
# Reuse the application's pinned runtime image so security updates cannot drift.
PYTHON = next(
    line.split()[1]
    for line in (ROOT / "Dockerfile").read_text().splitlines()
    if line.startswith("FROM python:")
)


def command(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def docker(*args: str) -> str:
    return command("docker", *args)


def check_compose_proxy_addresses(config: dict[str, Any]) -> None:
    """Exercise production IPAM and service attachments in app-first order.

    The lifecycle fixture below starts containers directly. It cannot catch
    Compose/Docker dynamically allocating Caddy's trusted address to the app.
    Only process commands/images are replaced here; network configuration comes
    from the effective production Compose files, with no published host ports.
    """
    project = "groundline-proxy-ipam-" + uuid.uuid4().hex[:10]
    networks = {
        name: {key: value for key, value in network.items() if key != "name"}
        for name, network in config["networks"].items()
    }
    fixture = {
        "services": {
            service: {
                "image": PYTHON,
                "command": ["python", "-c", "import time; time.sleep(3600)"],
                "networks": config["services"][service]["networks"],
            }
            for service in ("app", "caddy")
        },
        "networks": networks,
    }
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        path = Path(directory) / "compose.json"
        path.write_text(json.dumps(fixture))
        compose = (
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            project,
            "-f",
            str(path),
        )
        try:
            # Starting app alone forces its IP allocation before Caddy exists.
            docker(*compose, "up", "-d", "--no-build", "app")
            docker(*compose, "up", "-d", "--no-build", "caddy")
            for service, expected in (
                ("app", "172.30.254.3"),
                ("caddy", "172.30.254.2"),
            ):
                container = docker(*compose, "ps", "-q", service)
                assert container, f"{service} did not start"
                state = json.loads(docker("inspect", container))[0]
                assert state["State"]["Running"], service
                assert (
                    state["NetworkSettings"]["Networks"][f"{project}_proxy"][
                        "IPAddress"
                    ]
                    == expected
                ), service
        finally:
            command("docker", *compose, "down", "--remove-orphans", check=False)
    print(
        "PASS: Compose starts app before Caddy without claiming its trusted IP",
        flush=True,
    )


def main() -> None:
    name = "groundline-https-" + uuid.uuid4().hex[:10]
    containers: list[str] = []
    volume = name + "-data"
    env = dict(
        os.environ,
        POSTGRES_PASSWORD="test",
        GROUNDLINE_DB_PASSWORD="test",
        GROUNDLINE_PUBLIC_IP="203.0.113.10",
        GROUNDLINE_ALLOWED_CIDR="203.0.113.11/32",
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
    assert (
        config["services"]["app"]["environment"]["FORWARDED_ALLOW_IPS"]
        == "172.30.254.2"
    )
    caddy_image = config["services"]["caddy"]["image"]
    assert [p["published"] for p in config["services"]["caddy"]["ports"]] == ["443"]
    assert not config["services"]["caddy"].get("environment")
    check_compose_proxy_addresses(config)
    docker("network", "create", name)
    try:
        network = json.loads(docker("network", "inspect", name))[0]
        subnet = ipaddress.ip_network(network["IPAM"]["Config"][0]["Subnet"])
        proxy_address = str(subnet.network_address + 10)
        address = str(subnet.network_address + 11)
        allowed_address = str(subnet.network_address + 12)
        denied_address = str(subnet.network_address + 13)
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
            backend = """import time, socket, select, threading
from socketserver import BaseRequestHandler, ThreadingTCPServer
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# Forward TLS unchanged to a different address: model EC2/Docker NAT. Caddy
# cannot select the certificate from its local socket address without SNI.
class Forward(BaseRequestHandler):
    def handle(self):
        with socket.create_connection(('caddy', 443), timeout=150) as upstream:
            peers = (self.request, upstream)
            while True:
                readable, _, _ = select.select(peers, [], [], 150)
                if not readable: return
                for source in readable:
                    data = source.recv(65536)
                    if not data: return
                    target = upstream if source is self.request else self.request
                    target.sendall(data)
forwarder = ThreadingTCPServer(('0.0.0.0', 443), Forward)
threading.Thread(target=forwarder.serve_forever, daemon=True).start()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/slow': time.sleep(125)
        self.send_response(200)
        self.end_headers()
        body = b'ok'
        if self.path == '/client-ip':
            body = self.headers.get('X-Forwarded-For', '').encode()
        self.wfile.write(body)
    def do_POST(self):
        authorized = self.headers.get('Authorization') == 'Bearer fixture-key'
        status = 422 if authorized else 401
        self.send_response(status)
        self.end_headers()
    def log_message(self, *args): pass
ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
"""
            (work / "backend.py").write_text(backend)
            (work / "https_check.py").write_text(
                (ROOT / "scripts/https_check.py").read_text()
            )

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

            start(
                "app",
                PYTHON,
                "python",
                "/fixture/backend.py",
                options=("--ip", address),
            )
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
            allowed_client = start(
                "allowed",
                PYTHON,
                "python",
                "-c",
                "import time; time.sleep(86400)",
                options=("--ip", allowed_address),
            )
            denied_client = start(
                "denied",
                PYTHON,
                "python",
                "-c",
                "import time; time.sleep(86400)",
                options=("--ip", denied_address),
            )
            adapted: dict[str, Any] = json.loads(
                docker(
                    "run",
                    "--rm",
                    "-e",
                    f"GROUNDLINE_PUBLIC_IP={address}",
                    "-e",
                    f"GROUNDLINE_ALLOWED_CIDR={allowed_address}/32",
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
                options=("--ip", proxy_address, "-v", f"{volume}:/data"),
            )

            # Clients connect directly to Caddy while verifying the public IP,
            # modelling DNAT without a TCP proxy that hides the client's peer IP.
            # Pebble uses the separate forwarder; its peer is deliberately NOT
            # allowed for application access, so challenge validation tests the
            # TLS-versus-application boundary during issuance and renewal.
            def client(source: str, container: str = allowed_client) -> str:
                routing = f"""import socket
original_connect = socket.create_connection
def connect(target, *args, **kwargs):
    if target[0] == '{address}': target = ('{proxy_address}', target[1])
    return original_connect(target, *args, **kwargs)
socket.create_connection = connect
"""
                return docker("exec", container, "python", "-c", routing + source)

            # Trust the issuing root from the test CA's authenticated management API.
            fetch_root = """import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/fixture/cert.pem')
root = urllib.request.urlopen('https://pebble:15000/roots/0', context=ctx).read()
open('/tmp/issuer.pem', 'wb').write(root)
"""
            wait_for(lambda: client(fetch_root), "test CA readiness")
            client(fetch_root, denied_client)
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
            client(f"""import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
request = urllib.request.Request('https://{address}/client-ip',
    headers={{'X-Forwarded-For': '192.0.2.55'}})
assert urllib.request.urlopen(request, context=ctx).read() != b'192.0.2.55'
""")

            def denied(
                container: str = denied_client, claimed_address: str = allowed_address
            ) -> None:
                client(
                    f"""import ssl, urllib.request, urllib.error
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
for path in ('/health', '/docs', '/openapi.json', '/v1/omissions'):
    request = urllib.request.Request('https://{address}' + path,
        data=b'{{}}' if path.startswith('/v1/') else None,
        headers={{'Authorization': 'Bearer fixture-key',
                  'X-Forwarded-For': '{claimed_address}',
                  'X-Real-IP': '{claimed_address}',
                  'Forwarded': 'for={claimed_address}'}})
    try:
        urllib.request.urlopen(request, context=ctx)
    except urllib.error.HTTPError as error:
        assert error.code == 403, error.code
        assert error.read() == b'Forbidden'
    else:
        raise AssertionError('denied source accessed application route')
""",
                    container,
                )

            def authentication(container: str = allowed_client) -> None:
                client(
                    f"""import ssl, urllib.request, urllib.error
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
for key, status in (('invalid', 401), ('fixture-key', 422)):
    request = urllib.request.Request('https://{address}/v1/omissions', data=b'{{}}',
        headers={{'Authorization': 'Bearer ' + key}})
    try: urllib.request.urlopen(request, context=ctx)
    except urllib.error.HTTPError as error: assert error.code == status, error.code
    else: raise AssertionError('fixture authentication bypassed')
""",
                    container,
                )

            # Install the fixture root only in this disposable test client so
            # the production probe uses its ordinary trust-store code path.
            client(
                """import ssl
with open(ssl.get_default_verify_paths().cafile, 'ab') as trust:
    trust.write(open('/tmp/issuer.pem', 'rb').read())
""",
                denied_client,
            )

            def source_probe(
                mode: str, cidr: str, container: str = denied_client
            ) -> None:
                client(
                    f"""import os, runpy
os.environ['FORWARDED_ALLOW_IPS'] = '{proxy_address}'
probe = runpy.run_path('/fixture/https_check.py')
probe['check_source_filter']('{address}', '{mode}', '{cidr}')
""",
                    container,
                )

            denied()
            authentication()
            source_probe("restricted", f"{allowed_address}/32")
            print(
                "PASS: restricted health/docs/API routes deny a second source, "
                "including valid keys and spoofed forwarded headers",
                flush=True,
            )
            docker("restart", proxy)
            assert wait_for(lambda: client(connect), "restart recovery") == first
            denied()
            print(
                "PASS: certificate and IP restriction retained "
                "across container restart",
                flush=True,
            )

            def renewed() -> str:
                fingerprint = client(connect)
                if fingerprint == first:
                    raise RuntimeError("waiting for a different served certificate")
                return fingerprint

            wait_for(renewed, "automatic renewal and loading", seconds=180)
            denied()
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
            docker("network", "disconnect", name, ca)
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
        assert connection.connect_ex(('{proxy_address}', port)) != 0, port
""")
            docker("network", "connect", "--alias", "pebble", name, ca)
            wait_for(lambda: client(connect), "recovery after CA outage", seconds=300)
            print(
                "PASS: expiry fails closed; renewal recovers after CA outage",
                flush=True,
            )
            denied()
            # A tester-IP change reloads the filter without restarting Caddy
            # or replacing its certificate/database storage.
            updated = json.dumps(adapted).replace(
                f"{allowed_address}/32", f"{denied_address}/32"
            )
            (work / "updated.json").write_text(updated)
            docker(
                "exec", proxy, "caddy", "reload", "--config", "/fixture/updated.json"
            )
            denied(allowed_client, denied_address)
            authentication(denied_client)
            print(
                "PASS: tester-IP-only reload revokes the old source "
                "and admits the new source",
                flush=True,
            )
            unrestricted = json.dumps(adapted).replace(
                f"{allowed_address}/32", "0.0.0.0/0"
            )
            assert unrestricted != json.dumps(adapted)
            (work / "unrestricted.json").write_text(unrestricted)
            docker(
                "exec",
                proxy,
                "caddy",
                "reload",
                "--config",
                "/fixture/unrestricted.json",
            )
            client(
                f"""import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/tmp/issuer.pem')
assert urllib.request.urlopen('https://{address}/health', context=ctx).read() == b'ok'
""",
                denied_client,
            )
            authentication(denied_client)
            source_probe("unrestricted", "0.0.0.0/0")
            print(
                "PASS: unrestricted mode admits a second source "
                "while retaining API-key checks",
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
