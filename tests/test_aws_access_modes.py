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
  *rev-parse*) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
esac
""",
        "curl": '#!/bin/sh\necho called >>"$CURL_CALLS"\necho 198.51.100.20\n',
    }.items():
        tool = checkout / "bin" / name
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


@pytest.mark.parametrize("mode", [None, "tester_http", "https"])
def test_deploy_selects_exact_mode_and_only_http_discovers_tester(
    checkout: Path, mode: str | None
):
    settings(checkout, mode)
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"}
    )
    result, world = invoke(checkout, "aws-deploy", world)
    assert result.returncode == 0, result.stdout + result.stderr
    plans = [call for call in world["calls"] if "plan" in call]
    assert len(plans) == 1
    assert f"access_mode={mode or 'tester_http'}" in plans[0]
    assert (checkout / "curl-calls").exists() == (mode != "https")
    assert ("publicly reachable over HTTPS" in result.stdout) == (mode == "https")


def test_invalid_mode_cannot_apply_or_discover_ip(checkout: Path):
    settings(checkout, "public_http")
    result, world = invoke(checkout, "aws-deploy", aws_fakes.new_world())
    assert result.returncode != 0
    assert "access_mode" in result.stderr
    assert not any("plan" in call for call in world["calls"])
    assert not (checkout / "curl-calls").exists()


def stub_children(checkout: Path):
    for script in ("aws-deploy", "aws-smoke-test"):
        path = checkout / "scripts" / script
        path.write_text(f'#!/bin/sh\ntouch "{checkout}/{script}-called"\n')


@pytest.mark.parametrize("choice,mode", [("", "https"), ("2", "tester_http")])
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


def test_keeping_legacy_server_ignores_new_public_mode(checkout: Path):
    settings(checkout, "https")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "repository_url": "https://example.com/repo.git",
        },
        existing={"i-test": "running"},
    )
    result, _ = invoke(checkout, "aws-up", world, "\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preserves tester_http" in result.stderr
    assert not (checkout / "aws-deploy-called").exists()
    assert (checkout / "aws-smoke-test-called").exists()
