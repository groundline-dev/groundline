"""Exercise access selection through the actual shell scripts, in a spare checkout."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests import aws_fakes

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "infra").mkdir()
    shutil.copy2(ROOT / "infra/terraform.tfvars.example", tmp_path / "infra")
    return tmp_path


def invoke(checkout: Path, script: str, world: dict[str, Any], answer: str = ""):
    world_file = checkout / "world.json"
    world_file.write_text(json.dumps(world))
    fake_env = aws_fakes.install(checkout / "bin", world_file)
    for name, source in {
        "git": """#!/bin/sh
case "$*" in
  'config user.email') echo alerts@example.com ;;
  'branch --show-current') echo main ;;
  show*) echo groundline-allowed-cidr ;;
  *rev-parse*) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
esac
""",
        "curl": '#!/bin/sh\necho called >>"$CURL_CALLS"\necho 198.51.100.20\n',
    }.items():
        tool = checkout / "bin" / name
        if name == "git" and world.get("missing_https"):
            source = source.replace(
                "case", 'if [ "$1" = cat-file ]; then exit 1; fi\ncase', 1
            )
        tool.write_text(source)
        tool.chmod(0o755)
    env = dict(
        os.environ,
        **fake_env,
        PATH=f"{checkout}/bin:{os.environ['PATH']}",
        CURL_CALLS=str(checkout / "curl-calls"),
        AWS_PROFILE="test",
        REPOSITORY_URL="https://github.com/example/groundline.git",
    )
    env.pop("GROUNDLINE_AWS_SCRIPT", None)
    env.pop("AWS_REGION", None)
    result = subprocess.run(
        [str(checkout / "scripts" / script)],
        cwd=checkout,
        env=env,
        input=answer,
        text=True,
        capture_output=True,
        timeout=30,
    )
    return result, json.loads(world_file.read_text())


def settings(checkout: Path, mode: str | None):
    text = 'deployment_name = "alice"\nbudget_alert_email = "alerts@example.com"\n'
    if mode is not None:
        text += f'access_mode = "{mode}"\n'
    (checkout / "infra/terraform.tfvars").write_text(text)


@pytest.mark.parametrize(
    "mode",
    [
        None,
        "tester_http",
        "https",
        "public_http",
        "unrestricted_typo",
        "restricted",
        "unrestricted",
    ],
)
def test_deploy_defaults_saves_and_discovers_only_the_restricted_tester(
    checkout: Path, mode: str | None
):
    settings(checkout, mode)
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"}
    )
    result, world = invoke(checkout, "aws-deploy", world)
    assert result.returncode == 0, result.stdout + result.stderr
    effective = "unrestricted" if mode == "unrestricted" else "restricted"
    plans = [call for call in world["calls"] if "plan" in call]
    assert len(plans) == 1
    assert f"access_mode={effective}" in plans[0]
    assert (checkout / "curl-calls").exists() == (effective == "restricted")
    assert (
        f'access_mode = "{effective}"'
        in (checkout / "infra/terraform.tfvars").read_text()
    )
    assert (
        "Unrecognized access_mode. Saved the IP-restricted HTTPS default; "
        "HTTP deployments are no longer supported. Changes to a running server "
        "require approving and applying a deployment plan." in result.stderr
    ) == (mode not in (None, "restricted", "unrestricted"))
    result, _ = invoke(checkout, "aws-deploy", world)
    assert result.returncode == 0, result.stderr
    assert "Saved the IP-restricted" not in result.stderr
    command = world["commands"][0]["script"]
    assert "caddy reload" in command
    assert f"refresh-address '{effective}'" in command


def stub_children(checkout: Path):
    for script in ("aws-deploy", "aws-smoke-test"):
        path = checkout / "scripts" / script
        path.write_text(f'#!/bin/sh\ntouch "{checkout}/{script}-called"\n')


@pytest.mark.parametrize("choice,mode", [("", "restricted"), ("2", "unrestricted")])
def test_new_wizard_asks_once_and_saves_choice(checkout: Path, choice: str, mode: str):
    stub_children(checkout)
    answers = f"\nalice\nalerts@example.com\n{choice}\n\n"
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f'access_mode = "{mode}"' in (checkout / "infra/terraform.tfvars").read_text()
    )
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recommended" not in result.stdout


def test_first_setup_eof_does_not_save_partial_configuration(checkout: Path):
    result, _ = invoke(
        checkout, "aws-up", aws_fakes.new_world(), "\nalice\nalerts@example.com\n"
    )
    assert result.returncode != 0
    assert not (checkout / "infra/terraform.tfvars").exists()


@pytest.mark.parametrize(
    "running_mode,answer,success",
    [("restricted", "\n\n", True), ("unrestricted", "\n2\n", False)],
)
def test_keep_requires_matching_mode(
    checkout: Path, running_mode: str, answer: str, success: bool
):
    settings(checkout, "restricted")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "repository_url": "https://example.com/repo.git",
            "access_mode": running_mode,
        },
        existing={"i-test": "running"},
    )
    result, _ = invoke(checkout, "aws-up", world, answer)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    assert not (checkout / "aws-deploy-called").exists()
    assert (checkout / "aws-smoke-test-called").exists() == success
    if not success:
        assert (
            "running access is unrestricted HTTPS; saved access is restricted HTTPS"
            in result.stderr
        )
        assert "database and API keys" in result.stdout
        assert "Keep your running server" not in result.stdout


def test_old_revision_cannot_be_deployed_in_https_mode(checkout: Path):
    settings(checkout, "restricted")
    result, world = invoke(
        checkout, "aws-deploy", aws_fakes.new_world(missing_https=True)
    )
    assert result.returncode != 0
    assert "doesn't include HTTPS support" in result.stderr
    assert not any("plan" in call for call in world["calls"])


def test_cancelling_deployment_does_not_apply(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(
        plan={
            "variables": {},
            "resource_changes": [
                {
                    "mode": "managed",
                    "type": "aws_instance",
                    "change": {"actions": ["create"], "after": {}},
                }
            ],
        }
    )
    result, world = invoke(checkout, "aws-deploy", world, "3\n")
    assert result.returncode != 0
    assert "cancelled: nothing changed" in result.stderr
    assert not any("apply" in call for call in world["calls"])


def test_failed_access_reload_is_not_success(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"},
        ssm_replies=[{"when": "caddy reload", "status": "Failed"}],
    )
    result, _ = invoke(checkout, "aws-deploy", world)
    assert result.returncode != 0
    assert "couldn't apply HTTPS access" in result.stderr
    assert (
        "Terraform configuration is unchanged" in result.stdout
    )  # Terraform alone was not sufficient
    assert "What next" not in result.stdout
