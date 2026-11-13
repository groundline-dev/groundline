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
        "curl": '#!/bin/sh\necho called >>"$CURL_CALLS"\necho "$FAKE_PUBLIC_IP"\n',
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
        FAKE_PUBLIC_IP=world.get("tester_ip", "198.51.100.20"),
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


@pytest.mark.parametrize("mode", [None, "restricted", "unrestricted"])
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
    example = checkout / "infra/terraform.tfvars.example"
    example.write_text(
        example.read_text().replace(
            'access_mode = "restricted"',
            'access_mode = "restricted" # keep this comment',
        )
    )
    answers = f"\nalice\nalerts@example.com\n{choice}\n\n"
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), answers)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = (checkout / "infra/terraform.tfvars").read_text()
    assert f'access_mode = "{mode}"' in saved
    assert "# keep this comment" in saved
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
            "allowed_cidr": "198.51.100.20/32",
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


@pytest.mark.parametrize("mode", ["tester_http", "https", "unrestriced", "public_http"])
def test_direct_deploy_rejects_invalid_settings_without_rewriting(
    checkout: Path, mode: str
):
    settings(checkout, mode)
    original = (checkout / "infra/terraform.tfvars").read_bytes()
    result, world = invoke(checkout, "aws-deploy", aws_fakes.new_world())
    assert result.returncode != 0
    assert "Run make aws-up" in result.stderr
    assert (checkout / "infra/terraform.tfvars").read_bytes() == original
    assert not any("plan" in call or "apply" in call for call in world["calls"])


@pytest.mark.parametrize("choice,mode", [("1", "restricted"), ("2", "unrestricted")])
def test_invalid_wizard_mode_requires_selection_and_preserves_comments(
    checkout: Path, choice: str, mode: str
):
    settings(checkout, "unrestriced")
    path = checkout / "infra/terraform.tfvars"
    path.write_text(path.read_text().rstrip() + " # keep this comment\n")
    stub_children(checkout)
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), f"\n{choice}\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unrecognized" in result.stderr
    assert f'access_mode = "{mode}"' in path.read_text()
    assert "# keep this comment" in path.read_text()
    assert 'deployment_name = "alice"' in path.read_text()
    assert (checkout / "aws-deploy-called").exists()


def test_invalid_wizard_eof_preserves_settings_and_does_not_deploy(checkout: Path):
    settings(checkout, "unrestriced")
    original = (checkout / "infra/terraform.tfvars").read_bytes()
    stub_children(checkout)
    result, world = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n")
    assert result.returncode != 0
    assert (checkout / "infra/terraform.tfvars").read_bytes() == original
    assert not (checkout / "aws-deploy-called").exists()
    assert not world["commands"]


@pytest.mark.parametrize("action", ["fail", "ask"])
@pytest.mark.parametrize("mode", [None, "restricted", "unrestricted", "invalid"])
def test_configured_mode_separates_value_from_notices(
    checkout: Path, action: str, mode: str | None
):
    settings(checkout, mode)
    path = checkout / "infra/terraform.tfvars"
    original = path.read_bytes()
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; source scripts/aws-common.sh; "
            'mode="$(configured_access_mode "$1")"; printf "%s\\n" "$mode"',
            "test",
            action,
        ],
        cwd=checkout,
        input="2\n",
        text=True,
        capture_output=True,
        timeout=10,
    )
    if mode == "invalid" and action == "fail":
        assert result.returncode != 0
        assert result.stdout == ""
        assert "Run make aws-up" in result.stderr
        assert path.read_bytes() == original
        return
    assert result.returncode == 0, result.stderr
    expected = "unrestricted" if mode in ("invalid", "unrestricted") else "restricted"
    assert result.stdout == f"{expected}\n"
    assert f'access_mode = "{expected}"' in path.read_text()
    if mode is None:
        assert (
            "No access_mode set. Saved the IP-restricted HTTPS default."
            in result.stderr
        )
    elif mode == "invalid":
        assert "1) IP-restricted HTTPS" in result.stderr
        assert "2) Unrestricted HTTPS" in result.stderr
        assert "ok: saved unrestricted HTTPS access" in result.stderr
    else:
        assert result.stderr == ""
        assert path.read_bytes() == original


@pytest.mark.parametrize("allowed", ["198.51.100.19/32", "unavailable"])
def test_changed_or_undiscoverable_ip_stops_after_choosing_keep(
    checkout: Path, allowed: str
):
    settings(checkout, "restricted")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "access_mode": "restricted",
            "allowed_cidr": allowed,
        },
        existing={"i-test": "running"},
    )
    if allowed == "unavailable":
        # The command is installed by invoke; change its output at the env boundary.
        world["tester_ip"] = "unavailable"
    result, world = invoke(checkout, "aws-up", world, "\n\n")
    assert result.returncode != 0
    assert not (checkout / "aws-deploy-called").exists()
    assert not (checkout / "aws-smoke-test-called").exists()
    assert not world["commands"]
    if allowed == "unavailable":
        assert "couldn't find" in result.stderr
    else:
        assert "REF=" + "a" * 40 + " make aws-deploy" in result.stderr


def test_changed_ip_can_decline_keep_and_deploy_new_code(checkout: Path):
    settings(checkout, "restricted")
    (checkout / "scripts/aws-smoke-test").write_text(
        f'#!/bin/sh\ntouch "{checkout}/aws-smoke-test-called"\n'
    )
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "b" * 40,
            "access_mode": "restricted",
            "allowed_cidr": "198.51.100.19/32",
            "auto_terminate_hours": "0",
        },
        existing={"i-test": "running"},
    )
    # Confirm the project, decline keeping the server, and keep the stored key.
    result, world = invoke(checkout, "aws-up", world, "\nn\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    plans = [call for call in world["calls"] if "plan" in call]
    assert len(plans) == 1
    assert "git_ref=" + "a" * 40 in plans[0]
    assert "tester_cidr=198.51.100.20/32" in plans[0]
    assert "access_mode=restricted" in plans[0]
    assert (checkout / "aws-smoke-test-called").exists()


def test_unrestricted_keep_does_not_discover_tester_ip(checkout: Path):
    settings(checkout, "unrestricted")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "access_mode": "unrestricted",
        },
        existing={"i-test": "running"},
    )
    result, _ = invoke(checkout, "aws-up", world, "\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (checkout / "curl-calls").exists()
