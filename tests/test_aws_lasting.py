"""Lasting deployments, through the actual shell scripts in a spare checkout.

A lasting deployment keeps its data, API keys, settings and address across
upgrades until someone deletes it. These check what make aws-up, aws-deploy
and aws-destroy do differently for one: the lifetime question, the folder
guard, the pre-upgrade snapshot, and teardown that keeps the data unless
DATA=delete.
"""

import json
from pathlib import Path
from typing import Any

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, stub_children

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

ACCOUNT = "123456789012"
RUNNING = {"instance_id": "i-test", "auto_terminate_hours": "0", "lifetime": "lasting"}


def lasting(checkout: Path, name: str = "staging", lifetime: str = "lasting"):
    """infra/terraform.tfvars as make aws-up writes it for a lasting deployment."""
    text = (
        f'deployment_name = "{name}"\n'
        'budget_alert_email = "alerts@example.com"\n'
        'access_mode = "unrestricted"\n'
        f'lifetime = "{lifetime}"\n'
    )
    if lifetime == "lasting":
        text += 'auto_terminate_hours = "0"\nbudget_amount = "35"\n'
    (checkout / "infra/terraform.tfvars").write_text(text)


def held(checkout: Path, name: str, lifetime: str, account: str = ACCOUNT):
    """infra/data/ state in this folder for deployment NAME, in ACCOUNT."""
    state = checkout / "infra/data/terraform.tfstate.d" / account / "terraform.tfstate"
    state.parent.mkdir(parents=True, exist_ok=True)
    outputs = {
        "volume_id": "vol-0123456789abcdef0",
        "deployment_name": name,
        "lifetime": lifetime,
    }
    state.write_text(
        json.dumps({"outputs": {key: {"value": v} for key, v in outputs.items()}})
    )


def calls(world: dict[str, Any], *command: str) -> list[list[str]]:
    return [
        call for call in world["calls"] if call[1 : 1 + len(command)] == list(command)
    ]


# --- Choosing a lasting deployment ---


def test_the_wizard_saves_a_lasting_deployment_with_no_time_limit(checkout: Path):
    stub_children(checkout)
    # Project, name, email, lasting, budget (default), access (default), and
    # no Anthropic API key to paste.
    answers = "\nstaging\nalerts@example.com\n2\n\n\n\n"
    world = aws_fakes.new_world(parameter_exists=False)

    result, _ = invoke(checkout, "aws-up", world, answers)

    assert result.returncode == 0, result.stdout + result.stderr
    saved = (checkout / "infra/terraform.tfvars").read_text()
    assert 'lifetime = "lasting"' in saved
    assert 'auto_terminate_hours = "0"' in saved
    assert 'budget_amount = "35"' in saved
    # Callers from anywhere need to reach a lasting deployment.
    assert 'access_mode = "unrestricted"' in saved
    assert "about $18 a month" in result.stdout
    assert "monthly spend limit" in result.stdout


def test_the_wizard_saves_nothing_if_stopped_after_the_lifetime(checkout: Path):
    result, _ = invoke(
        checkout, "aws-up", aws_fakes.new_world(), "\nstaging\nalerts@example.com\n2\n"
    )

    assert result.returncode != 0
    assert not (checkout / "infra/terraform.tfvars").exists()


def test_a_disposable_deployment_is_the_default(checkout: Path):
    stub_children(checkout)

    result, _ = invoke(
        checkout, "aws-up", aws_fakes.new_world(), "\nalice\nalerts@example.com\n\n\n\n"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    saved = (checkout / "infra/terraform.tfvars").read_text()
    assert 'lifetime = "disposable"' in saved
    assert "budget_amount" not in saved


# --- One folder, one lasting deployment ---


def test_a_folder_holding_a_lasting_deployment_refuses_another_name(checkout: Path):
    lasting(checkout, name="alice", lifetime="disposable")
    held(checkout, "staging", "lasting")

    result, world = invoke(checkout, "aws-deploy", aws_fakes.new_world())

    assert result.returncode != 0
    assert "manages the lasting deployment staging" in result.stderr
    assert "git worktree add" in result.stderr
    assert not any("plan" in call for call in world["calls"])


def test_a_folder_holding_a_lasting_deployment_refuses_another_project(checkout: Path):
    lasting(checkout)
    held(checkout, "staging", "lasting", account="999999999999")

    result, world = invoke(checkout, "aws-deploy", aws_fakes.new_world())

    assert result.returncode != 0
    assert "in account 999999999999" in result.stderr
    assert not any("plan" in call for call in world["calls"])


def test_a_disposable_deployment_cant_become_lasting(checkout: Path):
    lasting(checkout, name="alice")
    held(checkout, "alice", "disposable")

    result, world = invoke(checkout, "aws-deploy", aws_fakes.new_world())

    assert result.returncode != 0
    assert "can't become lasting" in result.stderr
    assert not any("plan" in call for call in world["calls"])


def test_the_folder_still_deploys_its_own_lasting_deployment(checkout: Path):
    lasting(checkout)
    held(checkout, "staging", "lasting")

    result, _ = invoke(checkout, "aws-deploy", aws_fakes.new_world(outputs=RUNNING))

    assert result.returncode == 0, result.stdout + result.stderr


# --- Sharing a project ---


def test_a_lasting_deployment_warns_when_it_shares_a_project(checkout: Path):
    lasting(checkout)
    stub_children(checkout)
    world = aws_fakes.new_world(
        running=[
            {"name": "alice", "lifetime": "disposable"},
            {"name": "staging", "lifetime": "lasting"},
        ]
    )

    result, _ = invoke(checkout, "aws-up", world, "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "other deployments run in" in result.stderr and "alice" in result.stderr
    assert "best in an AWS project of its own" in result.stderr


def test_a_disposable_deployment_warns_when_a_lasting_one_shares_its_project(
    checkout: Path,
):
    lasting(checkout, name="alice", lifetime="disposable")
    stub_children(checkout)
    world = aws_fakes.new_world(running=[{"name": "staging", "lifetime": "lasting"}])

    result, _ = invoke(checkout, "aws-up", world, "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the lasting deployment staging runs in" in result.stderr


# --- Upgrading ---


def test_an_upgrade_takes_a_snapshot_first_and_keeps_only_the_newest(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(outputs=RUNNING, snapshots=["snap-old"])

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    [created] = calls(world, "ec2", "create-snapshot")
    assert created[created.index("--volume-id") + 1] == "vol-0123456789abcdef0"
    assert world["snapshots"] == ["snap-00000000000000002"]
    assert "deleted the older snapshot snap-old" in result.stdout
    # Before the upgrade's migrations run.
    order = [" ".join(call) for call in world["calls"]]
    snapshot_at = next(i for i, c in enumerate(order) if "create-snapshot" in c)
    upgrade_at = next(
        i for i, c in enumerate(order) if "send-command" in c and "server/apply" in c
    )
    assert snapshot_at < upgrade_at
    # A lasting deployment's data volume never goes with its server.
    [marked] = calls(world, "ec2", "modify-instance-attribute")
    assert '"DeleteOnTermination":false' in " ".join(marked)


def test_a_disposable_upgrade_takes_no_snapshot(checkout: Path):
    lasting(checkout, name="alice", lifetime="disposable")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"}
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls(world, "ec2", "create-snapshot")


def test_a_failed_snapshot_stops_the_upgrade(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(outputs=RUNNING, data_outputs={"volume_id": ""})

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode != 0
    assert not any("server/apply" in c["script"] for c in world["commands"])


def test_a_lasting_deployment_runs_main_unless_you_type_its_name(checkout: Path):
    lasting(checkout)
    stub_children(checkout)
    world = aws_fakes.new_world(branch="fix-thing")

    # Project, keep the stored key, choose the branch, mistype the name.
    result, _ = invoke(checkout, "aws-up", world, "\n\n2\nstagin\n")

    assert result.returncode != 0
    assert "canceled: staging keeps running what it runs now" in result.stderr
    assert not (checkout / "aws-deploy-called").exists()


def test_what_next_says_destroy_keeps_a_lasting_deployments_data(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(
        outputs={**RUNNING, "app_url": "https://1.1.1.1", "access_mode": "unrestricted"}
    )

    result, _ = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "It's a lasting deployment, so it has no time limit" in result.stdout
    assert "make aws-destroy DATA=delete deletes those too" in result.stdout


# --- Tearing down ---

DESTROYABLE = {
    "instance_id": "i-test",
    "security_group_id": "sg-test",
    "iam_role_name": "groundline-instance-staging",
    "instance_profile_name": "groundline-instance-staging",
    "budget_name": "groundline-monthly-staging",
    "anthropic_parameter_name": "/groundline/staging/anthropic-api-key",
    "lifetime": "lasting",
}


def destroy(checkout: Path, world: dict[str, Any], answer: str, *args: str):
    script = checkout / "scripts" / "aws-destroy-with-args"
    script.write_text(
        f'#!/bin/sh\nexec "{checkout}/scripts/aws-destroy" {" ".join(args)}\n'
    )
    script.chmod(0o755)
    return invoke(checkout, script.name, world, answer)


def test_destroy_keeps_a_lasting_deployments_data_address_and_key(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(
        outputs=DESTROYABLE, existing={"i-test": "terminated"}, snapshots=["snap-1"]
    )

    result, world = destroy(checkout, world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert world["destroyed"] and not world["data_destroyed"]
    assert world["parameter_exists"]
    assert world["snapshots"] == ["snap-1"]
    assert "kept its data" in result.stdout
    assert "about $4.05 a month" in result.stdout
    assert "make aws-destroy DATA=delete" in result.stdout
    assert not any(
        c[0] == "terraform" and "-chdir=infra/data" in c and "plan" in c
        for c in world["calls"]
    )


def test_data_delete_needs_the_name_typed_exactly(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(outputs=DESTROYABLE, existing={"i-test": "terminated"})

    result, world = destroy(checkout, world, "stagin\n", "--data", "delete")

    assert result.returncode != 0
    assert "canceled: staging and its data are unchanged" in result.stderr
    assert not world["destroyed"] and not world["data_destroyed"]
    assert world["parameter_exists"]


def test_data_delete_deletes_everything_once_the_name_is_typed(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(
        outputs=DESTROYABLE, existing={"i-test": "terminated"}, snapshots=["snap-1"]
    )

    result, world = destroy(checkout, world, "staging\n1\n", "--data", "delete")

    assert result.returncode == 0, result.stdout + result.stderr
    assert world["destroyed"] and world["data_destroyed"]
    assert world["snapshots"] == []
    assert not world["parameter_exists"]
    assert "There's no backup" in result.stdout


def test_destroy_rejects_other_arguments(checkout: Path):
    lasting(checkout)

    result, world = destroy(checkout, aws_fakes.new_world(), "", "--data", "keep")

    assert result.returncode != 0
    assert "DATA=delete" in result.stderr
    assert not world["calls"] or not any("plan" in call for call in world["calls"])


def commit_change(running: str) -> dict[str, Any]:
    """A plan that moves the server from commit RUNNING to the one deployed."""
    tags = {"groundline-git-ref": running}
    return {
        "variables": {},
        "resource_changes": [
            {
                "mode": "managed",
                "type": "aws_instance",
                "change": {
                    "actions": ["update"],
                    "before": {"id": "i-test", "tags": tags},
                    "after": {"id": "i-test", "tags": {"groundline-git-ref": "a" * 40}},
                },
            }
        ],
    }


def test_the_plan_says_which_commit_an_upgrade_moves_to(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(
        outputs=RUNNING, plan=commit_change("b" * 40), newer_commit=True
    )

    result, _ = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "It's upgraded from bbbbbbb to aaaaaaa." in result.stdout
    assert "rolls it back" not in result.stdout


def test_the_plan_warns_that_an_older_commit_is_a_rollback(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(outputs=RUNNING, plan=commit_change("b" * 40))

    result, _ = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "aaaaaaa is older than bbbbbbb, so this rolls it back" in result.stdout
    assert "restore the pre-upgrade snapshot first" in result.stdout


def test_status_says_a_kept_lasting_deployment_has_its_data(checkout: Path):
    lasting(checkout)
    world = aws_fakes.new_world(
        data_outputs={**aws_fakes.DATA_OUTPUTS, "lifetime": "lasting"}
    )

    result, _ = invoke(checkout, "aws-status", world)

    assert result.returncode != 0
    assert "its data and address (1.1.1.1) are kept" in result.stderr
