"""Run the AWS scripts against stand-in `aws` and `terraform` commands.

These check the scripts' own logic on paths that are slow or costly to cause
on a real server: the test API key is revoked even when a check fails, the
live smoke test stops before doing anything when the Anthropic parameter is
missing, the teardown deletes the deployment's Anthropic parameter and fails
when something survived or AWS can't say, and a server that terminated itself
gets the reminder to run make aws-destroy. They also check the layout rules
in scripts/aws-common.sh that a test can see: every run ends with a blank line,
and each command that leaves the server running ends with "What next".
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
PROJECT = ROOT / "scripts" / "aws-project"
STATUS = ROOT / "scripts" / "aws-status"
RESET_DEADLINE = ROOT / "scripts" / "aws-reset-deadline"

INSTANCE = "i-0123456789abcdef0"
GROUP = "sg-0123456789abcdef0"
KEY_ID = "5b0b8f9e-6f4e-4d8a-9c55-0b7f0e3c1a2d"
KEY = "gl_smoketestsecret"
PARAMETER = "/groundline/alice/anthropic-api-key"

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
        # Set, so the scripts don't read the profile make aws-up saved here.
        "AWS_PROFILE": "test",
    }
    env.pop("AWS_REGION", None)
    # Unset, so the script counts as the one you ran and prints what_next.
    env.pop("GROUNDLINE_AWS_SCRIPT", None)
    finished = subprocess.run(
        [str(script), *args],
        cwd=ROOT,
        env=env,
        input=answer,
        # One stream, so the output is in the order a terminal shows it.
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )
    return Run(
        finished.returncode,
        finished.stdout,
        json.loads(world_path.read_text()),
    )


def smoke_world(app: FakeApp, **fields: Any) -> dict[str, Any]:
    fields.setdefault("existing", {INSTANCE: "running"})
    return aws_fakes.new_world(
        outputs={
            "instance_id": INSTANCE,
            "region": "eu-north-1",
            "app_url": app.url,
            "anthropic_parameter_name": PARAMETER,
        },
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
    "anthropic_parameter_name": PARAMETER,
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


def test_status_shows_the_deadline_and_how_to_reset_it(tmp_path: Path):
    in_two_hours = str(int(time.time()) + 2 * 3600)

    result = run(STATUS, tmp_path, deadline_world("running", in_two_hours))

    assert result.returncode == 0, result.output
    assert "terminates itself" in result.output
    assert "h " in result.output and "from now" in result.output
    assert "make aws-reset-deadline" in result.output


def test_status_says_when_there_is_no_time_limit(tmp_path: Path):
    result = run(STATUS, tmp_path, deadline_world("running", "none"))

    assert result.returncode == 0, result.output
    assert "no time limit" in result.output


def test_status_reminds_to_destroy_a_server_that_terminated(tmp_path: Path):
    result = run(STATUS, tmp_path, deadline_world("terminated", "0"))

    assert result.returncode != 0, result.output
    assert "make aws-destroy" in result.output
    assert result.sent_commands() == []


def test_reset_deadline_moves_the_deadline_to_the_limit_from_now_by_default(
    tmp_path: Path,
):
    in_eight_hours = str(int(time.time()) + 8 * 3600)

    result = run(RESET_DEADLINE, tmp_path, deadline_world("running", in_eight_hours))

    assert result.returncode == 0, result.output
    [command] = result.sent_commands()
    assert "+ 28800" in command, command
    assert "-gt" in command, "it should never move the deadline earlier"
    assert "terminates itself" in result.output


def test_reset_deadline_refuses_more_hours_than_the_limit(tmp_path: Path):
    result = run(RESET_DEADLINE, tmp_path, deadline_world("running", "0"), "9")

    assert result.returncode != 0, result.output
    assert "1 to 8" in result.output
    assert result.sent_commands() == []


def test_reset_deadline_reminds_to_destroy_a_server_that_terminated(tmp_path: Path):
    result = run(RESET_DEADLINE, tmp_path, deadline_world("terminated", "0"))

    assert result.returncode != 0, result.output
    assert "make aws-destroy" in result.output
    assert result.sent_commands() == []


def deleted_parameters(result: Run) -> list[str]:
    """The parameters each `aws ssm delete-parameter` named."""
    return [
        call[call.index("--name") + 1]
        for call in result.world["calls"]
        if call[:3] == ["aws", "ssm", "delete-parameter"]
    ]


def test_destroy_checks_everything_is_gone_and_deletes_the_parameter(tmp_path: Path):
    world = aws_fakes.new_world(
        outputs=DESTROY_OUTPUTS, existing={INSTANCE: "terminated"}
    )

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode == 0, result.output
    assert f"delete your server ({INSTANCE}) and its data" in result.output
    assert result.world["destroyed"]
    assert deleted_parameters(result) == [PARAMETER]
    assert not result.world["parameter_exists"]
    assert "Revoke it in the Anthropic Console" in result.output


def test_destroy_passes_when_no_anthropic_key_was_stored(tmp_path: Path):
    world = aws_fakes.new_world(
        outputs=DESTROY_OUTPUTS,
        existing={INSTANCE: "terminated"},
        parameter_exists=False,
    )

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode == 0, result.output
    assert "no Anthropic API key was stored" in result.output


def test_destroy_needs_no_terraform_tfvars_settings(tmp_path: Path):
    """The fake Terraform, like the real one, fails when a required variable has
    no value, and the script can't count on infra/terraform.tfvars being there."""
    world = aws_fakes.new_world(
        outputs=DESTROY_OUTPUTS, existing={INSTANCE: "terminated"}
    )

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode == 0, result.output
    assert "No value for required variable" not in result.output


def test_destroy_fails_when_the_key_cannot_be_deleted_and_nothing_else_is_left(
    tmp_path: Path,
):
    """With no resources in Terraform's state, a key that couldn't be deleted
    still fails the run. Terraform names the key's parameter in an output, so the
    test doesn't depend on a deployment_name in the developer's tfvars file."""
    world = aws_fakes.new_world(
        outputs={"anthropic_parameter_name": PARAMETER},
        delete_parameter_error="AccessDeniedException",
    )

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode != 0, result.output
    assert "AccessDeniedException" in result.output
    assert "may still be stored" in result.output


def test_destroy_fails_when_the_instance_is_still_running(tmp_path: Path):
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS, existing={INSTANCE: "running"})

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode != 0, result.output
    assert INSTANCE in result.output


def test_an_expired_sign_in_says_to_run_make_aws_project(tmp_path: Path):
    """Not the AWS CLI's own advice to run aws login, which signs in another profile."""
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS, error="ExpiredToken")

    result = run(DESTROY, tmp_path, world, answer="1\n")

    assert result.returncode != 0, result.output
    assert "Run make aws-project to sign in again" in result.output
    assert "aws login" not in result.output


def test_destroy_deletes_nothing_when_cancelled(tmp_path: Path):
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS)

    result = run(DESTROY, tmp_path, world, answer="3\n")

    assert result.returncode != 0, result.output
    assert "cancelled: nothing changed" in result.output
    assert not result.world["destroyed"]
    assert deleted_parameters(result) == []


@pytest.mark.parametrize(("answer", "destroyed"), [("2\n1\n", True), ("2\n2\n", False)])
def test_destroy_asks_again_after_showing_the_full_plan(
    tmp_path: Path, answer: str, destroyed: bool
):
    world = aws_fakes.new_world(
        outputs=DESTROY_OUTPUTS, existing={INSTANCE: "terminated"}
    )

    result = run(DESTROY, tmp_path, world, answer=answer)

    assert "# aws_instance.server must be replaced" in result.output
    assert "# forces replacement" in result.output
    assert "Plan: 1 to add, 0 to change, 1 to destroy." in result.output
    assert 'instance_type      = "t4g.small"' not in result.output
    assert result.output.count("Show Terraform's plan first") == 1
    assert (result.returncode == 0) == destroyed, result.output
    assert result.world["destroyed"] == destroyed


def test_destroy_deletes_nothing_when_enter_is_pressed(tmp_path: Path):
    world = aws_fakes.new_world(outputs=DESTROY_OUTPUTS)

    result = run(DESTROY, tmp_path, world, answer="\n")

    assert result.returncode != 0, result.output
    assert "Enter a number from 1 to 3" in result.output
    assert not result.world["destroyed"]
    assert deleted_parameters(result) == []


def test_deployment_name_is_empty_without_terraform_tfvars(tmp_path: Path):
    """With no infra/terraform.tfvars, such as before make aws-up writes it,
    deployment_name returns nothing instead of stopping the script. It runs in
    an empty folder, so the developer's own terraform.tfvars can't hide this."""
    finished = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; '
            'name="$(deployment_name)"; echo "name=$name"',
            "_",
            str(ROOT / "scripts" / "aws-common.sh"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "name="


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:alice/groundline.git",
        "git@github.com:alice/groundline",
        "ssh://git@github.com/alice/groundline.git",
        "https://github.com/alice/groundline",
        "https://github.com/alice/groundline.git",
    ],
)
def test_the_server_clones_a_remote_over_https(remote: str):
    """The server clones without signing in, so an SSH remote, such as a fork,
    becomes its HTTPS address."""
    finished = subprocess.run(
        ["bash", "-c", 'source scripts/aws-common.sh && https_url "$1"', "_", remote],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "https://github.com/alice/groundline.git"


WHAT_NEXT_END = "to remove what it leaves behind.\n\n"
"""How the What next block ends (what_next in scripts/aws-common.sh)."""


def assert_ends_with_what_next(output: str) -> None:
    assert "\nWhat next\n" in output, output
    assert output.endswith(WHAT_NEXT_END), output[-300:]


def test_aws_project_ends_with_the_commands_to_choose_from(tmp_path: Path):
    world = aws_fakes.new_world()

    result = run(PROJECT, tmp_path, world, answer="\n")

    assert result.returncode == 0, result.output
    assert "\nWhat next\n" in result.output
    for command in ("make aws-deploy", "make aws-status", "make aws-destroy"):
        assert command in result.output
    assert result.output.endswith("\n\n")


@pytest.mark.parametrize("script", [STATUS, RESET_DEADLINE])
def test_commands_that_leave_the_server_running_end_with_what_next(
    script: Path, tmp_path: Path
):
    in_two_hours = str(int(time.time()) + 2 * 3600)

    result = run(script, tmp_path, deadline_world("running", in_two_hours))

    assert result.returncode == 0, result.output
    assert_ends_with_what_next(result.output)


def test_a_passing_smoke_test_ends_with_what_next(app: FakeApp, tmp_path: Path):
    result = run(SMOKE_TEST, tmp_path, smoke_world(app))

    assert result.returncode == 0, result.output
    assert_ends_with_what_next(result.output)


@pytest.mark.parametrize(
    ("script", "world", "args", "answer"),
    [
        pytest.param(
            STATUS, deadline_world("terminated", "0"), (), "", id="status-fails"
        ),
        pytest.param(
            RESET_DEADLINE,
            deadline_world("running", "0"),
            ("9",),
            "",
            id="reset-deadline-refuses",
        ),
        pytest.param(
            DESTROY,
            aws_fakes.new_world(
                outputs=DESTROY_OUTPUTS, existing={INSTANCE: "terminated"}
            ),
            (),
            "1\n",
            id="destroy-succeeds",
        ),
        pytest.param(
            DESTROY,
            aws_fakes.new_world(outputs=DESTROY_OUTPUTS),
            (),
            "3\n",
            id="destroy-cancelled",
        ),
        pytest.param(
            DESTROY,
            aws_fakes.new_world(outputs=DESTROY_OUTPUTS),
            (),
            "",
            id="destroy-gets-no-answer",
        ),
    ],
)
def test_every_run_ends_with_a_blank_line(
    script: Path,
    world: dict[str, Any],
    args: tuple[str, ...],
    answer: str,
    tmp_path: Path,
):
    result = run(script, tmp_path, world, *args, answer=answer)

    assert result.output.endswith("\n\n"), result.output[-300:]


@pytest.mark.parametrize(
    "mode,url",
    [
        ("https", "http://1.1.1.1:8001"),
        ("https", "https://example.com"),
        ("tester_http", "https://1.1.1.1"),
        ("public_http", "http://1.1.1.1:8001"),
    ],
)
def test_smoke_rejects_mode_url_mismatch_before_requests(
    tmp_path: Path, mode: str, url: str
):
    world = aws_fakes.new_world(
        outputs={
            "instance_id": INSTANCE,
            "app_url": url,
            "access_mode": mode,
            "public_ip": "1.1.1.1",
        }
    )
    result = run(SMOKE_TEST, tmp_path, world)
    assert result.returncode != 0, result.output
    assert not result.sent_commands()
