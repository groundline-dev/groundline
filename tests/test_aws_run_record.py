"""Each run of make aws-up, aws-deploy or aws-destroy leaves a run record.

It's JSON beside the run's log, infra/runs/<time>_<account>_<deployment>_
<command>.json, readable only by you. It holds each decision and where it came
from, the plan in plain words and whether it was approved, the outcome and the
tool versions, and never a secret. Its path is the last thing the run prints,
however it ends, and the last 10 of each deployment are kept.
"""

import json
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, stub_children
from tests.test_aws_lasting import DESTROYABLE, destroy, lasting

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

RECORD_NAME = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ_123456789012_staging_destroy(_\d+)?\.json"
)


def disposable_world(**fields: Any) -> dict[str, Any]:
    return aws_fakes.new_world(
        outputs=dict(DESTROYABLE, lifetime="disposable"),
        existing={"i-test": "terminated"},
        **fields,
    )


def records(checkout: Path) -> list[Path]:
    return sorted((checkout / "infra/runs").glob("*.json"))


def only_record(checkout: Path) -> tuple[Path, dict[str, Any]]:
    [path] = records(checkout)
    return path, json.loads(path.read_text())


def test_a_run_records_its_decisions_plan_outcome_and_versions(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(outputs=DESTROYABLE, existing={"i-test": "terminated"})

    result, _ = destroy(checkout, world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    path, record = only_record(checkout)
    assert RECORD_NAME.fullmatch(path.name), path.name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert record["format_version"] == "1.0"
    assert record["command"] == "destroy"
    assert record["outcome"] == "succeeded"
    assert record["deployment"] == "staging"
    assert record["project"] == {"account": "123456789012", "region": "eu-north-1"}
    assert record["decisions"]["data"] == {"value": "keep", "source": "default"}
    assert record["plan"]["approved"] is True
    assert "delete your server (i-test)." in record["plan"]["summary"]
    assert record["tool"]["terraform"] == "1.17.1"
    assert record["tool"]["aws_cli"] == "aws-cli/2.31.0"
    assert record["log_path"] == str(path.with_suffix(".log").relative_to(checkout))
    assert record["started_at"] <= record["finished_at"]


def test_the_records_path_is_the_last_thing_printed(checkout: Path):
    lasting(checkout, lifetime="disposable")

    result, _ = destroy(checkout, disposable_world(), "1\n")

    path, _ = only_record(checkout)
    assert result.stdout.endswith(f"\nRun record: {path.relative_to(checkout)}\n\n")


def test_a_cancel_is_recorded_with_the_plan_it_turned_down(checkout: Path):
    lasting(checkout, lifetime="disposable")

    result, _ = destroy(checkout, disposable_world(), "3\n")

    assert result.returncode != 0
    path, record = only_record(checkout)
    assert record["outcome"] == "canceled"
    assert record["plan"]["approved"] is False
    assert record["plan"]["summary"]
    assert "log_path" not in record
    assert result.stderr.endswith(f"Run record: {path.relative_to(checkout)}\n\n")


def test_a_failure_is_recorded_and_points_to_the_log(checkout: Path):
    lasting(checkout, lifetime="disposable")

    result, _ = destroy(checkout, disposable_world(apply_error="boom"), "1\n")

    path, record = only_record(checkout)
    assert record["outcome"] == "failed"
    assert result.stderr.endswith(
        f"Log and run record: {path.relative_to(checkout)}\n\n"
    )


def test_the_last_10_records_of_each_deployment_are_kept(checkout: Path):
    lasting(checkout, lifetime="disposable")
    runs = checkout / "infra/runs"
    runs.mkdir(parents=True)
    old = [
        f"2026-01-{day:02d}T00-00-00Z_123456789012_staging_up" for day in range(1, 12)
    ]
    for stem in old:
        (runs / f"{stem}.json").write_text("{}")
        (runs / f"{stem}.log").write_text("")
    other = runs / "2026-01-01T00-00-00Z_123456789012_production_up.json"
    other.write_text("{}")

    destroy(checkout, disposable_world(), "1\n")

    kept = [p.name for p in records(checkout) if "_staging_" in p.name]
    assert len(kept) == 10
    # The two oldest went, with their logs; the new one is kept.
    assert f"{old[0]}.json" not in kept and f"{old[1]}.json" not in kept
    assert not (runs / f"{old[0]}.log").exists()
    assert any("_staging_destroy" in name for name in kept)
    assert other.exists()


def test_a_record_from_the_same_second_gets_a_suffix(checkout: Path):
    lasting(checkout, lifetime="disposable")
    runs = checkout / "infra/runs"
    runs.mkdir(parents=True)
    now = datetime.now(UTC)
    # Every second the run could start in already has a record.
    for offset in range(-1, 10):
        stamp = (now + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H-%M-%SZ")
        (runs / f"{stamp}_123456789012_staging_destroy.json").write_text("{}")

    destroy(checkout, disposable_world(), "1\n")

    assert any(p.name.endswith("_staging_destroy_2.json") for p in records(checkout))


def test_the_anthropic_api_key_never_reaches_the_record_or_log(checkout: Path):
    stub_children(checkout)
    secret = "sk-ant-api03-never-in-a-record"
    (checkout / ".env").write_text(f"ANTHROPIC_API_KEY={secret}\n")
    world = aws_fakes.new_world(parameter_exists=False)

    # Sign in, name, email, disposable, restricted, use the key from .env.
    result, _ = invoke(checkout, "aws-up", world, "\nalice\nalerts@example.com\n\n\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    [path] = sorted((checkout / "infra/runs").glob("*_alice_up.json"))
    record = json.loads(path.read_text())
    assert record["decisions"]["anthropic_key"] == {
        "value": "stored",
        "source": "default",
    }
    assert record["decisions"]["deployment_name"] == {
        "value": "alice",
        "source": "chosen",
    }
    assert record["decisions"]["lifetime"]["source"] == "default"
    assert record["decisions"]["git_ref"]["source"] == "default"
    for written in (checkout / "infra/runs").iterdir():
        assert secret not in written.read_text(), written.name
    assert secret[-12:] not in path.read_text()
