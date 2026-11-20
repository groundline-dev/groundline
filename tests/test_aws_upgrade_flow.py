"""make aws-up run again, and make aws-upgrade TO=<commit>.

Run again, make aws-up upgrades a running deployment, a lasting one to the
latest main, after listing the commits it adds; with nothing to change it says
it's already up to date and only checks. make aws-upgrade deploys one commit,
and needs TO=. If an upgrade of a lasting deployment that took a snapshot fails,
it goes back to the commit that ran before without asking, and exits with 1;
not for a 403 from restricted access, not for a disposable deployment, and only
once. A lasting deployment whose data was kept gets a new server from that
data. Cancelling says what exists.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, settings, stub_children
from tests.test_aws_lasting import RUNNING, lasting, upgrade_to

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

OLD = "b" * 40
NEW = "a" * 40  # what the fake git says every commit is
CREATE_SERVER: dict[str, Any] = {
    "variables": {},
    "resource_changes": [
        {
            "mode": "managed",
            "type": "aws_instance",
            "change": {"actions": ["create"], "after": {}},
        }
    ],
}


def running_world(**fields: Any) -> dict[str, Any]:
    """A lasting deployment running OLD, which main has moved on from."""
    defaults: dict[str, Any] = {
        "outputs": dict(
            RUNNING,
            git_ref=OLD,
            access_mode="unrestricted",
            app_url="https://203.0.113.7",
            repository_url="https://github.com/groundline-dev/groundline.git",
        ),
        "existing": {"i-test": "running"},
        "newer_commit": True,
    }
    defaults.update(fields)
    return aws_fakes.new_world(**defaults)


def stand_ins(checkout: Path, deploys: list[str], checks: list[str]):
    """Stand-in aws-deploy and aws-smoke-test. Each aws-deploy run does the
    next of DEPLOYS: ok, or fail, after taking a snapshot if it says so. Each
    check does the next of CHECKS: ok, fail, or 403."""
    (checkout / "deploy-actions").write_text("\n".join(deploys) + "\n")
    (checkout / "check-actions").write_text("\n".join(checks) + "\n")
    take_turn = (
        'n=$(( $(cat "{counter}" 2>/dev/null || echo 0) + 1 ))\n'
        'echo "$n" >"{counter}"\n'
        'action="$(sed -n "${{n}}p" "{actions}")"\n'
    )
    record = (
        'jq "{change}" "$GROUNDLINE_RUN_STATE" >"$GROUNDLINE_RUN_STATE.next" '
        '&& mv "$GROUNDLINE_RUN_STATE.next" "$GROUNDLINE_RUN_STATE"'
    )
    (checkout / "scripts/aws-deploy").write_text(
        "#!/bin/bash\n"
        + take_turn.format(
            counter=checkout / "deploy-count", actions=checkout / "deploy-actions"
        )
        + 'echo "$REF approved=${GROUNDLINE_PREAPPROVED:-}'
        + ' rolls_back=${GROUNDLINE_ROLLS_BACK:-}"'
        + f' >>"{checkout}/deploys"\n'
        + 'case "$action" in *snapshot*) '
        + record.format(change='.result.snapshot = \\"snap-1\\"')
        + " ;; esac\n"
        + 'case "$action" in *fail*) exit 1 ;; esac\n'
    )
    (checkout / "scripts/aws-smoke-test").write_text(
        "#!/bin/bash\n"
        + take_turn.format(
            counter=checkout / "check-count", actions=checkout / "check-actions"
        )
        + 'case "$action" in\n'
        + "  ok) exit 0 ;;\n"
        + "  403) "
        + record.format(change='.result.access_denied = \\"yes\\"')
        + "; exit 1 ;;\n"
        + "  *) exit 1 ;;\n"
        + "esac\n"
    )


def deploys(checkout: Path) -> list[list[str]]:
    path = checkout / "deploys"
    return (
        [line.split() for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def record(checkout: Path) -> dict[str, Any]:
    [path] = sorted((checkout / "infra/runs").glob("*.json"))
    return json.loads(path.read_text())


# --- Running again ---


def test_already_up_to_date_asks_nothing_and_only_checks(checkout: Path):
    lasting(checkout)
    stub_children(checkout)
    world = running_world(outputs=dict(running_world()["outputs"], git_ref=NEW))

    # No input at all: a question would fail for want of an answer.
    result, _ = invoke(checkout, "aws-up", world)

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "ok: Already up to date: staging runs the latest main, aaaaaaa, and "
        "infra/terraform.tfvars matches the server."
    ) in shown
    assert "Checking the running server, without calling the model." in shown
    assert "ok: all good: staging runs aaaaaaa at https://203.0.113.7" in shown
    assert not (checkout / "aws-deploy-called").exists()
    assert (checkout / "aws-smoke-test-called").exists()


def test_an_upgrade_lists_the_commits_it_adds(checkout: Path):
    lasting(checkout)
    stub_children(checkout)
    commits = [f"{index:07x} change number {index}" for index in range(12)]
    world = running_world(commit_log=commits)

    result, _ = invoke(checkout, "aws-up", world)

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "staging is a lasting deployment running bbbbbbb. Upgrading it to the latest "
        "main, aaaaaaa, adds 12 commits:"
    ) in shown
    assert "0000009 change number 9" in shown
    assert "000000a change number 10" not in shown
    assert "and 2 more" in shown
    assert "Waiting for the upgraded app to answer" in shown
    assert "make aws-upgrade TO=<commit>" in shown
    assert "ok: staging is up and running aaaaaaa at https://203.0.113.7" in shown


def test_upgrading_without_to_says_how_to_upgrade_to_the_latest(checkout: Path):
    lasting(checkout)

    result, _ = invoke(checkout, "aws-upgrade", running_world())

    assert result.returncode == 2
    assert (
        "make aws-upgrade needs TO=<commit>. To upgrade to the latest, run make aws-up."
        in aws_fakes.flat(result.stderr)
    )
    assert not (checkout / "infra/runs").exists()


@pytest.mark.parametrize(
    ("newer", "says"),
    [
        (True, "it goes back to bbbbbbb automatically"),
        (False, "aaaaaaa is an older commit. So no snapshot is taken"),
    ],
)
def test_upgrading_to_a_commit_is_recorded_as_chosen(
    checkout: Path, newer: bool, says: str
):
    lasting(checkout)
    stub_children(checkout)

    # The fake git says main doesn't have a newer commit, so it asks for the
    # deployment's name.
    result, _ = invoke(
        checkout,
        upgrade_to(checkout, "4f1e2a9"),
        running_world(newer_commit=newer),
        "staging\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert says in aws_fakes.flat(result.stdout)
    [path] = (checkout / "infra/runs").glob("*.json")
    assert path.name.endswith("_staging_upgrade.json")
    run = json.loads(path.read_text())
    assert run["command"] == "upgrade"
    assert run["decisions"]["git_ref"] == {
        "value": NEW,
        "name": "4f1e2a9",
        "source": "chosen",
    }


# --- Going back after a failed upgrade ---


@pytest.mark.parametrize(
    ("deploy_actions", "check_actions"),
    [(["snapshot", "ok"], ["fail", "ok"]), (["snapshot fail", "ok"], ["ok"])],
    ids=["check fails", "upgrade step fails"],
)
def test_a_failed_upgrade_goes_back_without_asking(
    checkout: Path, deploy_actions: list[str], check_actions: list[str]
):
    lasting(checkout)
    stand_ins(checkout, deploy_actions, check_actions)

    result, _ = invoke(checkout, "aws-up", running_world())

    assert result.returncode == 1, result.stdout + result.stderr
    # The upgrade, which may go back, then back to OLD, approved already.
    assert deploys(checkout) == [
        [NEW, "approved=", "rolls_back=yes"],
        [OLD, "approved=yes", "rolls_back="],
    ]
    assert (
        "error: rolled back to bbbbbbb: the upgrade to aaaaaaa failed. If aaaaaaa "
        "changed the database, bbbbbbb may not work with it. The snapshot snap-1 from "
        "before the upgrade is kept"
    ) in aws_fakes.flat(result.stderr)
    run = record(checkout)
    assert run["outcome"] == "rolled-back"
    assert run["result"]["rolled_back_to"] == OLD


def test_a_403_from_restricted_access_doesnt_go_back(checkout: Path):
    lasting(checkout)
    stand_ins(checkout, ["snapshot"], ["403"])

    result, _ = invoke(checkout, "aws-up", running_world())

    assert result.returncode == 1
    assert deploys(checkout) == [[NEW, "approved=", "rolls_back=yes"]]
    assert "That isn't the upgrade's fault, so it wasn't rolled back." in (
        aws_fakes.flat(result.stderr)
    )
    assert record(checkout)["outcome"] == "failed"


def test_a_failed_rollback_stops_there(checkout: Path):
    lasting(checkout)
    stand_ins(checkout, ["snapshot", "ok", "ok"], ["fail", "fail", "fail"])

    result, _ = invoke(checkout, "aws-up", running_world())

    assert result.returncode == 1
    assert len(deploys(checkout)) == 2
    assert "going back to bbbbbbb failed too, so it stopped there rather than try" in (
        aws_fakes.flat(result.stderr)
    )
    run = record(checkout)
    assert run["outcome"] == "failed"
    assert run["result"]["rollback"] == "failed"


def test_a_disposable_deployment_doesnt_go_back(checkout: Path):
    lasting(checkout, name="alice", lifetime="disposable")
    stand_ins(checkout, ["snapshot"], ["fail"])
    world = running_world()
    world["outputs"]["lifetime"] = "disposable"

    result, _ = invoke(checkout, "aws-up", world)

    assert result.returncode == 1
    assert deploys(checkout) == [[NEW, "approved=", "rolls_back="]]
    assert "rolled back" not in result.stderr


def test_an_upgrade_that_stops_before_its_snapshot_doesnt_go_back(checkout: Path):
    lasting(checkout)
    stand_ins(checkout, ["fail"], [])

    result, _ = invoke(checkout, "aws-up", running_world())

    assert result.returncode == 1
    assert len(deploys(checkout)) == 1


def test_an_older_commit_that_fails_says_how_to_go_forward_again(checkout: Path):
    lasting(checkout)
    stand_ins(checkout, ["ok"], ["fail"])

    result, _ = invoke(
        checkout, upgrade_to(checkout, "1c9e5b2"), running_world(newer_commit=False)
    )

    assert result.returncode == 1
    assert len(deploys(checkout)) == 1
    assert (
        "It wasn't rolled back, because this moved to an older commit and took no "
        "snapshot. To go back to bbbbbbb, run make aws-upgrade TO=bbbbbbb."
    ) in aws_fakes.flat(result.stderr)


# --- A lasting deployment's kept data ---


def test_kept_data_gets_a_new_server_with_the_saved_settings(checkout: Path):
    lasting(checkout)
    stub_children(checkout)
    runs = checkout / "infra/runs"
    runs.mkdir(parents=True)
    (runs / "2026-11-12T10-00-00Z_123456789012_staging_destroy.json").write_text(
        json.dumps({"outcome": "succeeded", "finished_at": "2026-11-12T10:03:00Z"})
    )

    # make aws-destroy kept the data; no server runs. Enter keeps the settings.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n")

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "staging has no server. Its data volume and address were kept on 2026-11-12"
        in (shown)
    )
    assert "Deployment name staging (can't change once the deployment exists)" in shown
    assert "1) Email alerts to alerts@example.com" in shown
    assert "using the Anthropic API key stored for staging" in shown
    assert (
        "This creates a new server for staging and reattaches its kept data and "
        "address (kept since 2026-11-12)."
    ) in shown
    assert "A new server takes about 5 to 10 minutes to start." in shown
    assert (checkout / "aws-deploy-called").exists()


# --- Cancelling says what exists ---


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        (
            {
                "outputs": {**RUNNING, "access_mode": "unrestricted"},
                "existing": {"i-test": "running"},
            },
            "canceled: staging keeps running what it runs now",
        ),
        ({}, "canceled: no server was started. staging's data and address are"),
        ({"data_outputs": {}}, "canceled: nothing was created."),
    ],
    ids=["running", "kept data", "nothing"],
)
def test_cancelling_the_plan_says_what_exists(
    checkout: Path, fields: dict[str, Any], says: str
):
    lasting(checkout)
    world = aws_fakes.new_world(plan=CREATE_SERVER, **fields)

    result, world = invoke(checkout, "aws-deploy", world, "3\n")

    assert result.returncode != 0
    assert says in aws_fakes.flat(result.stderr), result.stdout + result.stderr
    assert not any("apply" in call for call in world["calls"])


# --- Replacing the Anthropic API key ---


def test_replace_key_asks_for_a_new_one_and_upgrades(checkout: Path):
    lasting(checkout)
    (checkout / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-api03-a-new-one\n")
    (checkout / "scripts/aws-deploy").write_text(
        f'#!/bin/bash\nenv >"{checkout}/deploy-env"\ncat <&3 >/dev/null\n'
    )
    (checkout / "scripts/aws-smoke-test").write_text("#!/bin/sh\n")
    world = running_world(outputs=dict(running_world()["outputs"], git_ref=NEW))

    # Use the key from .env.
    result, _ = invoke(checkout, "aws-up", world, "\n", REPLACE_KEY="1")

    assert result.returncode == 0, result.stdout + result.stderr
    environment = (checkout / "deploy-env").read_text()
    assert "GROUNDLINE_KEY_ON_FD3=yes" in environment
    assert "GROUNDLINE_KEY_OVERWRITE=yes" in environment
    assert record(checkout)["decisions"]["anthropic_key"]["value"] == "replaced"


def test_a_rollback_applies_its_plan_without_asking(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(plan=CREATE_SERVER)

    # No input: a question would fail for want of an answer.
    result, world = invoke(checkout, "aws-deploy", world, GROUNDLINE_PREAPPROVED="yes")

    # The fake's server has no outputs after it's created, so the run stops
    # after the apply; what matters is that it applied without asking.
    assert "The plan you approved said it would do this if the upgrade failed" in (
        aws_fakes.flat(result.stdout)
    )
    assert any("apply" in call for call in world["calls"])


# --- A disposable deployment whose time ran out ---


def test_an_expired_disposable_deployment_shows_its_saved_settings(checkout: Path):
    lasting(checkout, name="alice", lifetime="disposable")
    stub_children(checkout)

    # Its server terminated itself; Enter deploys it again.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n")

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert "alice's server has terminated, probably because its time limit passed." in (
        shown
    )
    assert "from your settings in infra/terraform.tfvars" in shown
    assert "2) Deployment name alice" in shown
    assert (checkout / "aws-deploy-called").exists()


# --- One approval for a first deploy ---

CREATE_DATA: dict[str, Any] = {
    "variables": {},
    "resource_changes": [
        {
            "mode": "managed",
            "type": kind,
            "change": {"actions": ["create"], "after": {"size": 5}},
        }
        for kind in ("aws_ebs_volume", "aws_eip")
    ],
}


def first_deploy_world(server_plan: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Nothing exists yet: the data plan creates the volume and address, and
    applying each plan adds its outputs. FIELDS change any of that."""
    defaults: dict[str, Any] = {
        "data_outputs": {},
        "data_plan": CREATE_DATA,
        "data_pending_outputs": dict(aws_fakes.DATA_OUTPUTS),
        "plan": server_plan,
        "pending_outputs": {"instance_id": "i-new", "auto_terminate_hours": "8"},
    }
    defaults.update(fields)
    return aws_fakes.new_world(**defaults)


def applies(world: dict[str, Any]) -> list[str]:
    return [
        next(arg for arg in call if arg.startswith("-chdir="))
        for call in world["calls"]
        if call[0].endswith("terraform") and "apply" in call
    ]


def test_a_first_deploy_asks_once(checkout: Path):
    settings(checkout, "restricted")

    # One answer only: a second question would fail for want of one.
    result, world = invoke(
        checkout, "aws-deploy", first_deploy_world(CREATE_SERVER), "1\n"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "then create your server, running aaaaaaa, for about $0.024 an hour." in shown
    )
    assert (
        "You approved this above, and it only creates things, so it goes ahead "
        "without asking again."
    ) in shown
    assert applies(world) == ["-chdir=infra/data", "-chdir=infra"]


def test_a_server_plan_that_replaces_something_still_asks(checkout: Path):
    settings(checkout, "restricted")
    replace: dict[str, Any] = {
        "variables": {},
        "resource_changes": [
            {
                "mode": "managed",
                "type": "aws_security_group",
                "change": {"actions": ["delete", "create"], "before": {"id": "sg-1"}},
            }
        ],
    }

    result, world = invoke(checkout, "aws-deploy", first_deploy_world(replace), "1\n")

    assert result.returncode != 0
    assert "goes ahead without asking again" not in result.stdout
    assert applies(world) == ["-chdir=infra/data"]


def test_a_running_server_still_asks_about_its_own_plan(checkout: Path):
    settings(checkout, "restricted")
    world = first_deploy_world(
        CREATE_SERVER,
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"},
        existing={"i-test": "running"},
        data_outputs=dict(aws_fakes.DATA_OUTPUTS),
    )

    result, world = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode != 0
    assert "then create your server" not in result.stdout
    assert applies(world) == ["-chdir=infra/data"]
