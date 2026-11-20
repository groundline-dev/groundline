"""make aws-up stores a new Anthropic API key only once the plan is approved.

aws-up keeps the key in its shell and hands it to aws-deploy on a pipe, file
descriptor 3: not in an argument, an environment variable or a file. aws-deploy
stores it once the server's plan is approved, before Terraform applies it, so
cancelling stores nothing, and a cancelled run that would replace a key keeps
the old one. With nothing to approve, it's stored after the apply.
"""

from pathlib import Path
from typing import Any

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, settings

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

KEY = "sk-ant-api03-only-on-a-pipe"
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


def deploy_with_key(checkout: Path, world: dict[str, Any], answer: str, **env: str):
    """Run aws-deploy as make aws-up does when it has a new key for it."""
    wrapper = checkout / "scripts" / "aws-deploy-with-key"
    exports = "".join(f"export {name}={value}\n" for name, value in env.items())
    wrapper.write_text(
        "#!/bin/bash\n"
        f"{exports}"
        f'GROUNDLINE_KEY_ON_FD3=yes exec "{checkout}/scripts/aws-deploy" '
        f"3< <(printf '%s' '{KEY}')\n"
    )
    wrapper.chmod(0o755)
    return invoke(checkout, wrapper.name, world, answer)


def calls_named(world: dict[str, Any], *names: str) -> list[int]:
    return [
        index
        for index, call in enumerate(world["calls"])
        if all(name in call for name in names)
    ]


def test_cancelling_the_plan_stores_nothing(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(
        plan=CREATE_SERVER, data_outputs={}, parameter_exists=False
    )

    result, world = deploy_with_key(checkout, world, "3\n")

    assert result.returncode != 0
    assert "canceled: nothing changed" in result.stderr
    assert not calls_named(world, "put-parameter")
    assert not world["parameter_exists"]


def test_approving_stores_the_key_before_terraform_applies(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(
        plan=CREATE_SERVER, data_outputs={}, parameter_exists=False
    )

    result, world = deploy_with_key(checkout, world, "1\n")

    [stored] = calls_named(world, "put-parameter")
    [applied] = calls_named(world, "apply", "-chdir=infra")
    plans = calls_named(world, "plan", "-chdir=infra")
    assert max(plans) < stored < applied
    assert world["stored_key_length"] == len(KEY)
    assert "ok: stored it in Test project (123456789012)'s Parameter Store" in (
        aws_fakes.flat(result.stdout)
    )
    # Never in the AWS CLI's arguments: only on its stdin.
    assert not any(KEY in " ".join(call) for call in world["calls"])


def test_replacing_a_key_overwrites_only_once_approved(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(plan=CREATE_SERVER, data_outputs={})

    canceled, after_cancel = deploy_with_key(
        checkout, world, "3\n", GROUNDLINE_KEY_OVERWRITE="yes"
    )
    assert canceled.returncode != 0
    assert not calls_named(after_cancel, "put-parameter")

    _, approved = deploy_with_key(
        checkout, world, "1\n", GROUNDLINE_KEY_OVERWRITE="yes"
    )
    [stored] = calls_named(approved, "put-parameter")
    assert "--overwrite" in approved["calls"][stored]


def test_with_nothing_to_approve_the_key_is_still_stored(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"},
        parameter_exists=False,
    )

    result, world = deploy_with_key(checkout, world, "")

    assert result.returncode == 0, result.stdout + result.stderr
    [stored] = calls_named(world, "put-parameter")
    # Before the upgrade, whose infra/server/apply reads the key.
    [upgrade] = [
        index
        for index, call in enumerate(world["calls"])
        if call[1:3] == ["ssm", "send-command"] and "server/apply" in " ".join(call)
    ]
    assert stored < upgrade


def test_aws_up_hands_the_key_over_on_a_pipe_not_the_environment(checkout: Path):
    # A stand-in aws-deploy that says what it was given.
    (checkout / "scripts/aws-deploy").write_text(
        f'#!/bin/bash\nenv >"{checkout}/deploy-env"\ncat <&3 >"{checkout}/deploy-fd3"\n'
    )
    (checkout / "scripts/aws-smoke-test").write_text("#!/bin/sh\n")
    (checkout / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    world = aws_fakes.new_world(parameter_exists=False)

    # Name alice, Enter keeps the other settings, use the key from .env.
    result, world = invoke(checkout, "aws-up", world, "2\nalice\n\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "deploy-fd3").read_text() == KEY
    assert KEY not in (checkout / "deploy-env").read_text()
    assert "GROUNDLINE_KEY_ON_FD3=yes" in (checkout / "deploy-env").read_text()
    # aws-up itself stored nothing: that's aws-deploy's job, after approval.
    assert not calls_named(world, "put-parameter")
    assert "stored once you approve the plan in step 2" in aws_fakes.flat(result.stdout)
