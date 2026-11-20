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


def invoke(
    checkout: Path,
    script: str,
    world: dict[str, Any],
    answer: str = "",
    **extra_env: str,
):
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
        if name == "git" and world.get("branch"):
            source = source.replace("echo main ;;", f"echo {world['branch']} ;;", 1)
        if name == "git" and world.get("newer_commit"):
            source = source.replace(
                "case", 'if [ "$1" = merge-base ]; then exit 1; fi\ncase', 1
            )
        if name == "git" and world.get("commit_log"):
            # git log --oneline and rev-list --count, for the commits an
            # upgrade adds.
            lines = world["commit_log"]
            listed = " ".join(f"'{line}'" for line in lines)
            source = source.replace(
                'case "$*" in',
                f"case \"$*\" in\n  log*) printf '%s\\n' {listed} ;;"
                f"\n  rev-list*) echo {len(lines)} ;;",
                1,
            )
        if name == "git" and world.get("missing_apply"):
            source = source.replace(
                "case",
                'case "$*" in *infra/server/apply*) exit 1 ;; esac\ncase',
                1,
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
        **extra_env,
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


def server_plans(world: dict[str, Any]) -> list[list[str]]:
    """The terraform plan calls for the server (infra/), not its data volume."""
    return [
        call for call in world["calls"] if "plan" in call and "-chdir=infra" in call
    ]


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
    plans = server_plans(world)
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


@pytest.mark.parametrize("choice,mode", [("1", "restricted"), ("2", "unrestricted")])
def test_new_wizard_asks_once_and_saves_choice(checkout: Path, choice: str, mode: str):
    stub_children(checkout)
    example = checkout / "infra/terraform.tfvars.example"
    example.write_text(
        example.read_text().replace(
            'access_mode = "restricted"',
            'access_mode = "restricted" # keep this comment',
        )
    )
    # Change Access (row 6), then Enter keeps the rest and the stored key.
    answers = f"6\n{choice}\n\n\n"
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), answers)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = (checkout / "infra/terraform.tfvars").read_text()
    assert f'access_mode = "{mode}"' in saved
    assert "# keep this comment" in saved
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recommended" not in result.stdout


def test_first_setup_eof_does_not_save_partial_configuration(checkout: Path):
    # Change the name, then no answer to the summary.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "2\nalice\n")
    assert result.returncode != 0
    assert not (checkout / "infra/terraform.tfvars").exists()


@pytest.mark.parametrize(
    "running_mode,upgraded", [("restricted", False), ("unrestricted", True)]
)
def test_a_matching_server_is_only_checked_and_another_mode_is_applied(
    checkout: Path, running_mode: str, upgraded: bool
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
    # Nothing to answer: running again upgrades, or only checks.
    result, _ = invoke(checkout, "aws-up", world)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "aws-deploy-called").exists() == upgraded
    assert (checkout / "aws-smoke-test-called").exists()
    shown = aws_fakes.flat(result.stdout)
    if upgraded:
        assert "Applying your settings to it keeps its code, data and address." in shown
    else:
        assert "Already up to date: alice runs the latest main, aaaaaaa" in shown


@pytest.mark.parametrize(
    "saved,running,matches",
    [
        ({"GROUNDLINE_MAX_SOURCE_CHARS": 150000}, {}, False),
        ({}, {"GROUNDLINE_MAX_SOURCE_CHARS": 150000}, False),
        (
            {"GROUNDLINE_MAX_SOURCE_CHARS": 150000},
            {"GROUNDLINE_MAX_SOURCE_CHARS": 150000},
            True,
        ),
    ],
)
def test_saved_app_settings_that_differ_are_applied(
    checkout: Path, saved: dict[str, int], running: dict[str, int], matches: bool
):
    settings(checkout, "restricted")
    if saved:
        # Real Terraform reads it from here; the fake console answers from the world.
        with (checkout / "infra/terraform.tfvars").open("a") as tfvars:
            tfvars.write("app_settings = { GROUNDLINE_MAX_SOURCE_CHARS = 150000 }\n")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "repository_url": "https://example.com/repo.git",
            "access_mode": "restricted",
            "allowed_cidr": "198.51.100.20/32",
            "app_settings": running,
        },
        existing={"i-test": "running"},
        saved_app_settings=saved,
    )

    result, _ = invoke(checkout, "aws-up", world)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "aws-smoke-test-called").exists()
    assert (checkout / "aws-deploy-called").exists() != matches


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
        },
        # With a data volume, cancelling says it stays (test_aws_lasting).
        data_outputs={},
    )
    result, world = invoke(checkout, "aws-deploy", world, "3\n")
    assert result.returncode != 0
    assert "canceled: nothing was created." in result.stderr
    assert not any("apply" in call for call in world["calls"])


def test_failed_access_reload_is_not_success(checkout: Path):
    settings(checkout, "restricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"},
        ssm_replies=[{"when": "caddy reload", "status": "Failed"}],
    )
    result, _ = invoke(checkout, "aws-deploy", world)
    assert result.returncode != 0
    assert "couldn't upgrade the server to aaaaaaa. Retry make aws-deploy" in (
        aws_fakes.flat(result.stderr)
    )
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
    # The access mode, then Enter keeps the summary.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), f"{choice}\n\n")
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
    result, world = invoke(checkout, "aws-up", aws_fakes.new_world(), "")
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
        assert "1) Only this computer can reach it" in result.stderr
        assert "2) Open to any IP over HTTPS" in result.stderr
        assert "ok: saved unrestricted HTTPS access" in result.stderr
    else:
        assert result.stderr == ""
        assert path.read_bytes() == original


@pytest.mark.parametrize("allowed", ["198.51.100.19/32", "unavailable"])
def test_a_changed_ip_is_applied_and_an_unknown_one_stops(checkout: Path, allowed: str):
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
    result, world = invoke(checkout, "aws-up", world)
    assert not world["commands"]
    if allowed == "unavailable":
        assert result.returncode != 0
        assert "couldn't find" in result.stderr
        assert not (checkout / "aws-deploy-called").exists()
    else:
        # The server allows only its old IP, so it's updated in place.
        assert result.returncode == 0, result.stdout + result.stderr
        assert (checkout / "aws-deploy-called").exists()
        assert (checkout / "aws-smoke-test-called").exists()


def test_a_changed_ip_and_new_code_are_one_upgrade(checkout: Path):
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
    # Nothing to answer: Terraform's plan changes only what it shows.
    result, world = invoke(checkout, "aws-up", world)
    assert result.returncode == 0, result.stdout + result.stderr
    plans = server_plans(world)
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


def test_deploy_makes_the_data_volume_first_then_attaches_the_server(checkout: Path):
    settings(checkout, "unrestricted")
    with (checkout / "infra/terraform.tfvars").open("a") as tfvars:
        tfvars.write('instance_type = "t4g.medium"\n')
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"}
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    plans = [call for call in world["calls"] if "plan" in call]
    assert ["-chdir=infra/data" in call for call in plans] == [True, False]
    assert "deployment_name=alice" in plans[0]
    assert "instance_type=t4g.medium" in plans[0]
    assert "the data volume and address are unchanged" in result.stdout
    # A disposable server's data volume goes with it when it terminates itself.
    marks = [
        call
        for call in world["calls"]
        if call[1:3] == ["ec2", "modify-instance-attribute"]
    ]
    assert len(marks) == 1
    assert '"DeleteOnTermination":true' in " ".join(marks[0])


def test_the_data_plan_checks_the_saved_app_settings(checkout: Path):
    # infra/data/'s plan rejects bad settings, so nothing is created first.
    settings(checkout, "unrestricted")
    with (checkout / "infra/terraform.tfvars").open("a") as tfvars:
        tfvars.write("app_settings = { GROUNDLINE_MAX_SOURCE_CHARS = 150000 }\n")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"},
        saved_app_settings={"GROUNDLINE_MAX_SOURCE_CHARS": 150000},
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    [data_plan] = [
        c for c in world["calls"] if "plan" in c and "-chdir=infra/data" in c
    ]
    assert 'app_settings={"GROUNDLINE_MAX_SOURCE_CHARS":150000}' in data_plan


def test_unreadable_app_settings_stop_before_anything_is_created(checkout: Path):
    settings(checkout, "unrestricted")
    with (checkout / "infra/terraform.tfvars").open("a") as tfvars:
        tfvars.write('app_settings = { GROUNDLINE_MAX_SOURCE_CHARS = "lots" }\n')
    # The fake console fails without saved_app_settings, as Terraform does here.
    world = aws_fakes.new_world()

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode != 0
    assert "app_settings in infra/terraform.tfvars aren't valid" in result.stderr
    assert not any("plan" in call for call in world["calls"])


def test_deploy_refuses_a_server_made_before_data_volumes(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-old", "auto_terminate_hours": "8"}, data_outputs={}
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode != 0
    assert "Run make aws-destroy, then make aws-up" in result.stderr
    assert not any("plan" in call or "apply" in call for call in world["calls"])


def test_deploy_refuses_a_plan_that_would_delete_the_data_volume(checkout: Path):
    settings(checkout, "unrestricted")
    deleting: dict[str, Any] = {
        "variables": {},
        "resource_changes": [
            {
                "mode": "managed",
                "type": "aws_ebs_volume",
                "change": {
                    "actions": ["delete", "create"],
                    "before": {"id": "vol-0123456789abcdef0"},
                },
            }
        ],
    }
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "8"},
        data_plan=deleting,
    )

    result, world = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode != 0
    assert "would delete the deployment's data volume or address" in result.stderr
    assert not any(call[1:2] == ["apply"] for call in world["calls"])
    assert not world["data_destroyed"]


def test_deploy_refuses_a_commit_without_infra_server_apply(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(missing_apply=True)

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode != 0
    assert "too old to keep its data on a data volume" in result.stderr
    assert not any("plan" in call for call in world["calls"])


@pytest.mark.parametrize("hours", ["8", "0"])
def test_a_running_server_is_upgraded_in_place(checkout: Path, hours: str):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": hours}
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Upgrading your server (i-test) in place to aaaaaaa" in result.stdout
    assert "It kept its data and address" in result.stdout
    [upgrade] = [
        c["script"] for c in world["commands"] if "server/apply" in c["script"]
    ]
    steps = json.loads(upgrade)["commands"][0]
    commit = "a" * 40
    assert "refresh-address 'unrestricted' '0.0.0.0/0'" in steps
    assert (
        steps.index("refresh-address")
        < steps.index(
            f"git fetch --quiet 'https://github.com/example/groundline.git' '{commit}'"
        )
        < steps.index(f"git checkout --quiet --detach '{commit}'")
        < steps.index("infra/server/apply")
        < steps.index("caddy reload")
    )
    # A disposable server's time limit starts again, as a new server's would.
    assert ("3600)) >/var/lib/groundline/terminate-at.new" in steps) == (hours != "0")


def test_an_upgrade_that_reports_another_commit_is_not_success(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"},
        ssm_replies=[{"when": "server/apply", "stdout": "groundline: running bbbb\n"}],
    )

    result, _ = invoke(checkout, "aws-deploy", world)

    assert result.returncode != 0
    assert "didn't report running aaaaaaa" in result.stderr


def test_a_new_server_is_not_upgraded_over_ssm(checkout: Path):
    """It runs the commit from its first boot, so there's nothing to upgrade."""
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        pending_outputs={"instance_id": "i-new", "auto_terminate_hours": "8"},
        plan={
            "variables": {},
            "resource_changes": [
                {
                    "mode": "managed",
                    "type": "aws_instance",
                    "change": {"actions": ["create"], "before": None},
                }
            ],
        },
    )

    result, world = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "created your server" in result.stdout
    assert not any("server/apply" in c["script"] for c in world["commands"])


def test_a_settings_change_is_described_and_the_settings_are_listed(checkout: Path):
    settings(checkout, "unrestricted")
    before = {
        "Name": "groundline-alice",
        "groundline-setting-GROUNDLINE_MAX_BODY_BYTES": "1",
    }
    after = {
        "Name": "groundline-alice",
        "groundline-setting-GROUNDLINE_CHECK_TIMEOUT_SECONDS": "180",
    }
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "auto_terminate_hours": "0",
            "app_settings": {"GROUNDLINE_CHECK_TIMEOUT_SECONDS": 180},
        },
        plan={
            "variables": {},
            "resource_changes": [
                {
                    "mode": "managed",
                    "type": "aws_instance",
                    "change": {
                        "actions": ["update"],
                        "before": {"id": "i-test", "tags": before},
                        "after": {"id": "i-test", "tags": after},
                    },
                }
            ],
        },
    )

    result, world = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    # The server waits until its tags show these before writing .env.
    [upgrade] = [
        c["script"] for c in world["commands"] if "server/apply" in c["script"]
    ]
    assert (
        "GROUNDLINE_EXPECTED_SETTINGS='GROUNDLINE_CHECK_TIMEOUT_SECONDS=180' "
        "infra/server/apply" in upgrade
    )
    assert "update your server (i-test) in place" in result.stdout
    assert (
        "Its GROUNDLINE_CHECK_TIMEOUT_SECONDS becomes 180 (it was the default)."
        in result.stdout
    )
    assert "Its GROUNDLINE_MAX_BODY_BYTES goes back to the default (it was 1)." in (
        result.stdout
    )
    assert "  · GROUNDLINE_CHECK_TIMEOUT_SECONDS = 180" in result.stdout


def test_a_deploy_with_no_settings_says_it_uses_the_defaults(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "auto_terminate_hours": "0",
            "app_settings": {},
        }
    )

    result, world = invoke(checkout, "aws-deploy", world)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "It runs with the app's default settings" in result.stdout
    # The server waits until no setting tags are left.
    assert any(
        "GROUNDLINE_EXPECTED_SETTINGS='' infra/server/apply" in c["script"]
        for c in world["commands"]
    )


def test_a_larger_data_volume_is_described_as_growing(checkout: Path):
    settings(checkout, "unrestricted")
    world = aws_fakes.new_world(
        outputs={"instance_id": "i-test", "auto_terminate_hours": "0"},
        data_plan={
            "variables": {},
            "resource_changes": [
                {
                    "mode": "managed",
                    "type": "aws_ebs_volume",
                    "change": {
                        "actions": ["update"],
                        "before": {"id": "vol-1", "size": 5},
                        "after": {"id": "vol-1", "size": 20},
                    },
                }
            ],
        },
    )

    result, _ = invoke(checkout, "aws-deploy", world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "grow its data volume from 5 to 20 GiB, about $1.60 a month" in (
        result.stdout
    )
    assert "filesystem grows when the server is upgraded" in aws_fakes.flat(
        result.stdout
    )


def test_settings_are_compared_without_terraform_console_when_none_are_saved(
    checkout: Path,
):
    # Terraform's default is no settings, and console takes a few seconds.
    settings(checkout, "restricted")
    stub_children(checkout)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": "i-test",
            "git_ref": "a" * 40,
            "repository_url": "https://example.com/repo.git",
            "access_mode": "restricted",
            "allowed_cidr": "198.51.100.20/32",
            "app_settings": {},
        },
        existing={"i-test": "running"},
    )

    result, world = invoke(checkout, "aws-up", world, "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "aws-smoke-test-called").exists()
    assert not any("console" in call for call in world["calls"])
