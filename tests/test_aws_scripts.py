"""Run the AWS scripts against stand-in `aws` and `terraform` commands.

These check the scripts' own logic on paths that are slow or costly to cause
on a real server: the test API key is revoked even when a check fails, the
live smoke test stops before doing anything when the Anthropic parameter is
missing, the teardown check fails when something survived or AWS can't say,
and a server that terminated itself gets the reminder to run make aws-destroy.
A local HTTP server plays the app.

The stand-ins (tests/aws_fakes.py) answer the way the real tools do, but only
the real run against AWS (docs/deploying-to-aws.md) shows that the
scripts' AWS calls are right.
"""

import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests import aws_fakes

ROOT = Path(__file__).resolve().parents[1]
SMOKE_TEST = ROOT / "scripts" / "aws-smoke-test"
DESTROY = ROOT / "scripts" / "aws-destroy"
STATUS = ROOT / "scripts" / "aws-status"
EXTEND = ROOT / "scripts" / "aws-extend"

INSTANCE = "i-0123456789abcdef0"
GROUP = "sg-0123456789abcdef0"
KEY_ID = "5b0b8f9e-6f4e-4d8a-9c55-0b7f0e3c1a2d"
KEY = "gl_smoketestsecret"
PARAMETER = "/groundline/anthropic-api-key"

KEYS_CREATE_OUTPUT = (
    "Name: aws-smoke-test\n"
    f"ID: {KEY_ID}\n"
    "Expires: never\n"
    "Copy the API key now; it cannot be retrieved later.\n"
    f"API key: {KEY}\n"
)
"""What `groundline keys create` prints (src/groundline/keys_cli.py)."""


@dataclass
class FakeApp:
    """Answers /health with 200, POST /v1/omissions with what `statuses` gives
    for the Bearer token, and POST /v1/commissions with 200 for the test key and
    401 otherwise. Records each request."""

    statuses: dict[str, int] = field(default_factory=lambda: {KEY: 422, "default": 401})
    requests: list[tuple[str, str]] = field(default_factory=lambda: [])
    url: str = ""


@pytest.fixture
def app() -> Iterator[FakeApp]:
    fake = FakeApp()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def answer(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            fake.requests.append(("GET", self.path))
            self.answer(200 if self.path == "/health" else 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            fake.requests.append(("POST", self.path))
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if self.path == "/v1/omissions":
                self.answer(fake.statuses.get(token, fake.statuses["default"]))
            elif self.path == "/v1/commissions":
                self.answer(200 if token == KEY else 401)
            else:
                self.answer(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake
    server.shutdown()
    server.server_close()


@dataclass
class Run:
    returncode: int
    output: str
    world: dict[str, Any]

    def sent_commands(self) -> list[str]:
        """The shell commands each `aws ssm send-command` asked the server to run."""
        return [command["script"] for command in self.world["commands"]]


def run(
    script: Path,
    tmp_path: Path,
    world: dict[str, Any],
    *args: str,
    answer: str = "",
) -> Run:
    """Run the script against the stand-ins, typing answer at its prompts."""
    assert script.is_file(), f"{script.relative_to(ROOT)} is missing"
    world_path = tmp_path / "world.json"
    world_path.write_text(json.dumps(world))
    env = {
        **os.environ,
        **aws_fakes.install(tmp_path / "bin", world_path),
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
    }
    finished = subprocess.run(
        [str(script), *args],
        cwd=ROOT,
        env=env,
        input=answer,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return Run(
        finished.returncode,
        finished.stdout + finished.stderr,
        json.loads(world_path.read_text()),
    )


def smoke_world(app: FakeApp, **fields: Any) -> dict[str, Any]:
    fields.setdefault("existing", {INSTANCE: "running"})
    return aws_fakes.new_world(
        outputs={"instance_id": INSTANCE, "region": "eu-north-1", "app_url": app.url},
        ssm_replies=[
            {"when": "keys create", "stdout": KEYS_CREATE_OUTPUT},
            {"when": "keys revoke", "stdout": f"Revoked: {KEY_ID} (aws-smoke-test)\n"},
        ],
        **fields,
    )


def test_the_smoke_test_passes_and_revokes_its_key(app: FakeApp, tmp_path: Path):
    result = run(SMOKE_TEST, tmp_path, smoke_world(app))

    assert result.returncode == 0, result.output
    assert ("GET", "/health") in app.requests
    assert app.requests.count(("POST", "/v1/omissions")) == 2
    assert not any(path == "/v1/commissions" for _, path in app.requests)
    commands = result.sent_commands()
    assert any("keys create" in command for command in commands)
    assert any(f"keys revoke {KEY_ID}" in command for command in commands)


def test_the_smoke_test_fails_and_still_revokes_when_the_key_is_refused(
    app: FakeApp, tmp_path: Path
):
    """A working key left on the server is also left in SSM's command history."""
    app.statuses = {"default": 401}

    result = run(SMOKE_TEST, tmp_path, smoke_world(app))

    assert result.returncode != 0, result.output
    assert "401" in result.output
    assert any(f"keys revoke {KEY_ID}" in c for c in result.sent_commands())


def test_the_live_smoke_test_stops_first_without_the_anthropic_parameter(
    app: FakeApp, tmp_path: Path
):
    result = run(
        SMOKE_TEST, tmp_path, smoke_world(app, parameter_exists=False), "--live"
    )

    assert result.returncode != 0, result.output
    assert PARAMETER in result.output
    assert not result.sent_commands(), "it created a key before checking"
    assert not any(path == "/v1/commissions" for _, path in app.requests)


def test_the_live_smoke_test_sends_the_test_key_with_its_commission_check(
    app: FakeApp, tmp_path: Path
):
    """Commission checks need an API key, like omission checks (ALL-74)."""
    result = run(SMOKE_TEST, tmp_path, smoke_world(app), "--live")

    assert result.returncode == 0, result.output
    assert ("POST", "/v1/commissions") in app.requests
    assert any(f"keys revoke {KEY_ID}" in c for c in result.sent_commands())


DESTROY_OUTPUTS = {
    "instance_id": INSTANCE,
    "region": "eu-north-1",
    "security_group_id": GROUP,
    "iam_role_name": "groundline-instance",
    "instance_profile_name": "groundline-instance",
    "budget_name": "groundline-monthly",
}


def test_the_smoke_test_stops_with_a_reminder_when_the_server_terminated(
    app: FakeApp, tmp_path: Path
):
    result = run(
        SMOKE_TEST, tmp_path, smoke_world(app, existing={INSTANCE: "terminated"})
    )

    assert result.returncode != 0, result.output
    assert "make aws-destroy" in result.output
    assert app.requests == [], "it shouldn't wait for /health on a terminated server"
    assert result.sent_commands() == []


def deadline_world(state: str, deadline: str, **fields: Any) -> dict[str, Any]:
    """A deployed server in STATE whose deadline file holds DEADLINE."""
    return aws_fakes.new_world(
        outputs={
            "instance_id": INSTANCE,
            "region": "eu-north-1",
            "app_url": "http://203.0.113.7:8000",
            "auto_terminate_hours": "8",
        },
        existing={INSTANCE: state},
        ssm_replies=[{"when": "terminate-at", "stdout": f"{deadline}\n"}],
        **fields,
    )


def test_status_shows_the_deadline_and_how_to_extend_it(tmp_path: Path):
    in_two_hours = str(int(time.time()) + 2 * 3600)

    result = run(STATUS, tmp_path, deadline_world("running", in_two_hours))

    assert result.returncode == 0, result.output
    assert "terminates itself" in result.output
    assert "h " in result.output and "from now" in result.output
    assert "make aws-extend" in result.output


def test_status_says_when_there_is_no_time_limit(tmp_path: Path):
    result = run(STATUS, tmp_path, deadline_world("running", "none"))

    assert result.returncode == 0, result.output
    assert "no time limit" in result.output


def test_status_reminds_to_destroy_a_server_that_terminated(tmp_path: Path):
    result = run(STATUS, tmp_path, deadline_world("terminated", "0"))

    assert result.returncode != 0, result.output
    assert "make aws-destroy" in result.output
    assert result.sent_commands() == []


def test_extend_moves_the_deadline_to_the_limit_from_now_by_default(tmp_path: Path):
    in_eight_hours = str(int(time.time()) + 8 * 3600)

    result = run(EXTEND, tmp_path, deadline_world("running", in_eight_hours))

    assert result.returncode == 0, result.output
    [command] = result.sent_commands()
    assert "+ 28800" in command, command
    assert "-gt" in command, "it should never move the deadline earlier"
    assert "terminates itself" in result.output


def test_extend_refuses_more_hours_than_the_limit(tmp_path: Path):
    result = run(EXTEND, tmp_path, deadline_world("running", "0"), "9")

    assert result.returncode != 0, result.output
    assert "1 to 8" in result.output
    assert result.sent_commands() == []


def test_extend_reminds_to_destroy_a_server_that_terminated(tmp_path: Path):
    result = run(EXTEND, tmp_path, deadline_world("terminated", "0"))

    assert result.returncode != 0, result.output
    assert "make aws-destroy" in result.output
    assert result.sent_commands() == []


def test_destroy_checks_everything_is_gone_and_keeps_the_parameter(tmp_path: Path):
    world = aws_fakes.new_world(
        outputs=DESTROY_OUTPUTS, existing={INSTANCE: "terminated"}
    )

    result = run(DESTROY, tmp_path, world, answer="yes\n")

    assert result.returncode == 0, result.output
    assert f"delete your server ({INSTANCE}) and its data" in result.output
    assert result.world["destroyed"]
    assert f"aws ssm delete-parameter --name {PARAMETER}" in result.output
    assert not any("delete-parameter" in call for call in result.world["calls"])


def test_destroy_fails_when_the_instance_is_still_running(tmp_path: Path):
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS, existing={INSTANCE: "running"})

    result = run(DESTROY, tmp_path, world, answer="yes\n")

    assert result.returncode != 0, result.output
    assert INSTANCE in result.output


def test_destroy_fails_when_aws_cannot_say_what_is_left(tmp_path: Path):
    """An expired sign-in isn't the same as "not found": the check can't pass."""
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS, error="ExpiredToken")

    result = run(DESTROY, tmp_path, world, answer="yes\n")

    assert result.returncode != 0, result.output
    assert "ExpiredToken" in result.output


def test_destroy_deletes_nothing_unless_the_answer_is_yes(tmp_path: Path):
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS)

    result = run(DESTROY, tmp_path, world, answer="no\n")

    assert result.returncode != 0, result.output
    assert "cancelled: nothing changed" in result.output
    assert not result.world["destroyed"]
