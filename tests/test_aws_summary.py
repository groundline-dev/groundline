"""make aws-up's first run shows one summary of guessed settings, and make
aws-settings shows the saved ones.

The guesses follow the branch: on main, a lasting deployment called production
that any IP can reach; on another branch, a disposable one named after you, for
this computer only. A row is changed by its number, and Enter keeps them all.
Changing the lifetime guesses again the rows you left alone. A name a server or
kept data already uses isn't guessed, and is refused when typed. make
aws-settings locks what can't change once the deployment exists, and applies a
change on the commit the server already runs.
"""

import getpass
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, stub_children

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

RUNNING_COMMIT = "b" * 40
LASTING_SETTINGS = (
    'deployment_name = "production"\n'
    'budget_alert_email = "alerts@example.com"\n'
    'access_mode = "unrestricted"\n'
    'lifetime = "lasting"\n'
    'auto_terminate_hours = "0"\n'
    'budget_amount = "35"\n'
)


def saved(checkout: Path) -> str:
    return (checkout / "infra/terraform.tfvars").read_text()


def record(checkout: Path) -> dict[str, Any]:
    [path] = sorted((checkout / "infra/runs").glob("*.json"))
    return json.loads(path.read_text())


def last_summary(output: str) -> str:
    """The summary as last shown, flattened."""
    return aws_fakes.flat(output.split("Here they are with your change:")[-1])


def your_username() -> str:
    name = re.sub("[^a-z0-9-]", "", getpass.getuser().lower())[:20]
    return name if re.match("[a-z]", name) else "test"


# --- The guesses ---


def test_on_main_the_guess_is_a_lasting_deployment_called_production(
    checkout: Path,
):
    stub_children(checkout)

    # Enter keeps every guess, then the stored Anthropic API key.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "2) Deployment name production (names its AWS resources, such as "
        "groundline-production)" in shown
    )
    assert "3) Email alerts to alerts@example.com (git config user.email)" in shown
    assert "(guessed because you're on main)" in shown
    assert "7) Branch main, from groundline-dev/groundline" in shown
    text = saved(checkout)
    assert 'deployment_name = "production"' in text
    assert 'lifetime = "lasting"' in text
    assert 'access_mode = "unrestricted"' in text
    assert 'budget_amount = "35"' in text
    decisions = record(checkout)["decisions"]
    for name in ("deployment_name", "lifetime", "access_mode", "budget_amount"):
        assert decisions[name]["source"] == "default", name


def test_on_a_branch_the_guess_is_a_disposable_one_for_this_computer(
    checkout: Path,
):
    # No answer to the summary, so it stops there.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(branch="feature"), "")

    assert result.returncode != 0
    shown = aws_fakes.flat(result.stdout)
    assert f"2) Deployment name {your_username()} (names its AWS resources" in shown
    assert "4) Lifetime disposable: a test deployment" in shown
    assert "(guessed because you're on feature)" in shown
    assert "5) Budget $5 a month" in shown
    assert "6) Access only this computer can reach it (IP-restricted)" in shown
    assert "7) Branch your branch feature" in shown
    assert not (checkout / "infra/terraform.tfvars").exists()


def test_changing_the_lifetime_guesses_again_only_the_rows_you_left(
    checkout: Path,
):
    stub_children(checkout)

    # On a branch: a $20 budget, then a lasting lifetime; Enter keeps the rest
    # and the stored key.
    result, _ = invoke(
        checkout,
        "aws-up",
        aws_fakes.new_world(branch="feature"),
        "5\n20\n4\n2\n\n\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    shown = last_summary(result.stdout)
    assert "Deployment name production (changed to match the lifetime)" in shown
    assert "Budget $20 a month" in shown
    assert "Budget $20 a month: you'll get an email" in shown
    assert "API key (changed to match the lifetime)" in shown
    assert "Branch main, from groundline-dev/groundline (changed to match" in shown
    text = saved(checkout)
    assert 'budget_amount = "20"' in text
    assert 'access_mode = "unrestricted"' in text
    decisions = record(checkout)["decisions"]
    assert decisions["budget_amount"] == {"value": "20", "source": "chosen"}
    assert decisions["lifetime"] == {"value": "lasting", "source": "chosen"}
    # Guessed again, but nobody chose them.
    assert decisions["deployment_name"] == {"value": "production", "source": "default"}
    assert decisions["access_mode"]["source"] == "default"


# --- Names already in use ---


@pytest.mark.parametrize(
    "world",
    [
        {"running": [{"name": "production", "lifetime": "lasting"}]},
        {"kept_volumes": ["production"]},
    ],
    ids=["running server", "kept data"],
)
def test_a_name_in_use_is_not_guessed(checkout: Path, world: dict[str, Any]):
    stub_children(checkout)

    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(**world), "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "Deployment name production-2 (production is already used in this project)"
        in aws_fakes.flat(result.stdout)
    )
    assert 'deployment_name = "production-2"' in saved(checkout)


def test_a_name_in_use_is_refused_when_typed(checkout: Path):
    stub_children(checkout)
    world = aws_fakes.new_world(kept_volumes=["staging"])

    result, _ = invoke(checkout, "aws-up", world, "2\nstaging\nstaging2\n\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "staging is already used in this project: a server or kept data still has "
        "that name. Choose another, such as staging-2."
    ) in aws_fakes.flat(result.stdout)
    assert 'deployment_name = "staging2"' in saved(checkout)


def test_a_lasting_deployment_of_your_branch_needs_its_name_typed(checkout: Path):
    # Lasting, then your branch, then Enter, then the wrong name.
    result, _ = invoke(
        checkout,
        "aws-up",
        aws_fakes.new_world(branch="feature"),
        "4\n2\n7\n2\n\nprod\n",
    )

    assert result.returncode != 0
    assert (
        "Callers rely on the lasting deployment production. Deploying feature puts "
        "code that isn't merged into main in front of them."
    ) in aws_fakes.flat(result.stdout)
    assert "canceled: nothing was created." in result.stderr
    assert not (checkout / "infra/terraform.tfvars").exists()


# --- make aws-settings ---


def running_world(**fields: Any) -> dict[str, Any]:
    return aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": RUNNING_COMMIT,
            "repository_url": "https://github.com/groundline-dev/groundline.git",
            "app_url": "https://203.0.113.7",
        },
        existing={"i-test": "running"},
        **fields,
    )


def settings_checkout(checkout: Path, deploy: str = "") -> Path:
    """A lasting deployment's settings, and a stand-in aws-deploy that says
    what it was given; DEPLOY runs after that."""
    (checkout / "infra/terraform.tfvars").write_text(LASTING_SETTINGS)
    (checkout / "scripts/aws-deploy").write_text(
        "#!/bin/bash\n"
        f'echo "$REF" >"{checkout}/deploy-ref"\n'
        f'cp infra/terraform.tfvars "{checkout}/deploy-tfvars"\n'
        f"{deploy}\n"
    )
    (checkout / "scripts/aws-smoke-test").write_text("#!/bin/sh\n")
    return checkout


APPROVE = (
    'jq ".plan.approved = true" "$GROUNDLINE_RUN_STATE" >approved '
    '&& mv approved "$GROUNDLINE_RUN_STATE"'
)


def test_settings_lock_what_cant_change_and_keep_the_commit(checkout: Path):
    settings_checkout(checkout, APPROVE)

    # A $50 budget, then Enter.
    result, _ = invoke(checkout, "aws-settings", running_world(), "2\n50\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    shown = aws_fakes.flat(result.stdout)
    assert (
        "Deployment name production (can't change once the deployment exists)" in shown
    )
    assert "1) Email alerts to alerts@example.com" in shown
    assert "2) Budget $35 a month" in shown
    assert "3) Access open to any IP over HTTPS" in shown
    assert "Lifetime lasting" in shown and "1) Lifetime" not in shown
    assert "budget: $35 → $50 a month" in shown
    assert "ok: production runs with its new settings at https://203.0.113.7" in shown
    # Applied on the commit it runs, with the new budget saved first.
    assert (checkout / "deploy-ref").read_text().strip() == RUNNING_COMMIT
    assert 'budget_amount = "50"' in (checkout / "deploy-tfvars").read_text()
    assert 'budget_amount = "50"' in saved(checkout)
    decisions = record(checkout)["decisions"]
    assert decisions["budget_amount"] == {"value": "50", "source": "chosen"}
    assert decisions["deployment_name"]["source"] == "previous"
    assert decisions["git_ref"]["source"] == "previous"


def test_settings_put_the_file_back_if_the_plan_isnt_approved(checkout: Path):
    settings_checkout(checkout, "exit 1")

    # Access for this computer only, then Enter; aws-deploy stops unapproved.
    result, _ = invoke(checkout, "aws-settings", running_world(), "3\n1\n\n")

    assert result.returncode != 0
    assert 'access_mode = "restricted"' in (checkout / "deploy-tfvars").read_text()
    assert saved(checkout) == LASTING_SETTINGS


def test_settings_with_nothing_changed_deploy_nothing(checkout: Path):
    settings_checkout(checkout)

    result, _ = invoke(checkout, "aws-settings", running_world(), "\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: nothing changed: production keeps its settings." in result.stdout
    assert not (checkout / "deploy-ref").exists()
    assert record(checkout)["plan"]["summary"] == ["nothing to change"]


def test_settings_need_a_running_server(checkout: Path):
    settings_checkout(checkout)

    result, _ = invoke(checkout, "aws-settings", aws_fakes.new_world())

    assert result.returncode != 0
    assert "production has no server in Test project (123456789012)" in (
        aws_fakes.flat(result.stderr)
    )
    assert not (checkout / "deploy-ref").exists()
