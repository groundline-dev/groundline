"""Terraform's output goes to the run's log, and each apply is one named step.

The screen shows "Deleting the server and what it uses..." and, when it's done,
"ok: deleted the server and what it uses (6 resources, 2s)"; everything
Terraform printed is in infra/runs/, readable only by you. A failed apply shows
the end of the log and where the rest is. The first Ctrl-C lets Terraform
finish the change it's making, and says so; a second stops it at once.
"""

import json
import os
import re
import select
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from tests import aws_fakes
from tests.test_aws_access_modes import checkout
from tests.test_aws_lasting import DESTROYABLE, destroy, lasting

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

LOG_NAME = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ_123456789012_staging_destroy\.log"
)


def disposable_world(**fields: Any) -> dict[str, Any]:
    return aws_fakes.new_world(
        outputs=dict(DESTROYABLE, lifetime="disposable"),
        existing={"i-test": "terminated"},
        **fields,
    )


def run_logs(checkout: Path) -> list[Path]:
    return sorted((checkout / "infra/runs").glob("*.log"))


def test_each_apply_is_one_named_step_with_terraform_in_the_log(checkout: Path):
    lasting(checkout, lifetime="disposable")

    result, _ = destroy(checkout, disposable_world(), "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "\nDeleting the server and what it uses...\n" in result.stdout
    assert re.search(
        r"\n  ok: deleted the server and what it uses \(\d+ resources, \d+s\)\n",
        result.stdout,
    ), result.stdout
    assert "\nDeleting the saved data and address...\n" in result.stdout
    # Terraform's own output stays off the screen.
    assert "Apply complete" not in result.stdout + result.stderr
    [log] = run_logs(checkout)
    assert LOG_NAME.fullmatch(log.name), log.name
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    text = log.read_text()
    assert "$ terraform -chdir=infra apply" in text
    assert "Apply complete!" in text


def test_the_result_and_the_next_action_are_separate(checkout: Path):
    lasting(checkout, lifetime="disposable")

    result, _ = destroy(checkout, disposable_world(), "1\n")

    assert "ok: your deployment is deleted.\n\nTo deploy again, run make aws-up.\n" in (
        result.stdout
    )


def test_a_failed_apply_shows_the_end_of_the_log_and_where_it_is(checkout: Path):
    lasting(checkout, lifetime="disposable")
    world = disposable_world(apply_error="creating EC2 Instance: UnauthorizedOperation")

    result, _ = destroy(checkout, world, "1\n")

    assert result.returncode != 0
    [log] = run_logs(checkout)
    assert "Error: creating EC2 Instance: UnauthorizedOperation" in result.stderr
    # The last line points to the log and the run record, which share a name.
    record = log.with_suffix(".json").relative_to(checkout)
    assert result.stderr.endswith(f"Log and run record: {record}\n\n")


def interrupt(checkout: Path, world: dict[str, Any], times: int):
    """Run aws-destroy, approve its plan, and press Ctrl-C TIMES during the apply."""
    world_file = checkout / "world.json"
    world_file.write_text(json.dumps(world))
    env = dict(
        os.environ,
        **aws_fakes.install(checkout / "bin", world_file),
        PATH=f"{checkout}/bin:{os.environ['PATH']}",
        AWS_PROFILE="test",
    )
    env.pop("GROUNDLINE_AWS_SCRIPT", None)
    env.pop("GROUNDLINE_RUN_LOG", None)
    # A session of its own, so the test can send its Ctrl-C to the whole
    # process group, as a terminal does.
    process = subprocess.Popen(
        [str(checkout / "scripts" / "aws-destroy")],
        cwd=checkout,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin and process.stdout and process.stderr
    process.stdin.write(b"1\n")
    process.stdin.close()
    seen = b""
    deadline = time.monotonic() + 20
    while b"Deleting the server and what it uses..." not in seen:
        assert time.monotonic() < deadline, seen
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if ready:
            seen += os.read(process.stdout.fileno(), 4096)
    time.sleep(0.5)  # until the fake terraform is applying
    for _ in range(times):
        os.killpg(process.pid, signal.SIGINT)
        time.sleep(0.3)
    out, err = process.communicate(timeout=20)
    return (
        process.returncode,
        (seen + out).decode(),
        err.decode(),
        json.loads(world_file.read_text()),
    )


def test_the_first_ctrl_c_lets_terraform_finish_and_says_so(checkout: Path):
    lasting(checkout, lifetime="disposable")

    status, _, err, world = interrupt(checkout, disposable_world(apply_seconds=10), 1)

    assert status == 130, err
    # Terraform got the one Ctrl-C, not one from the terminal and one passed on.
    assert world["apply_interrupts"] == 1
    flat = aws_fakes.flat(err)
    assert "Stopping safely: Terraform is finishing the change it's making." in flat
    assert "canceled partway. Run make aws-destroy again to carry on" in flat


def test_a_second_ctrl_c_stops_terraform_at_once(checkout: Path):
    lasting(checkout, lifetime="disposable")

    status, _, err, world = interrupt(checkout, disposable_world(apply_seconds=10), 2)

    assert status == 130, err
    assert world["apply_interrupts"] == 2
    assert "Stopping at once." in err
