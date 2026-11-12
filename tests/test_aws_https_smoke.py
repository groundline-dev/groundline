"""Run the lifecycle script against fake AWS, SSM, TLS and child smoke boundaries."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import aws_fakes

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = "i-0123456789abcdef0"


def run(tmp_path: Path, restart: str = "", answer: str = "1\n", **changes: Any):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "infra").mkdir()
    lifecycle = dict(restarts=0, reboots=0, smokes=0, **changes)
    world = aws_fakes.new_world(
        outputs={
            "instance_id": INSTANCE,
            "region": "eu-north-1",
            "access_mode": changes.get("mode", "restricted"),
            "allowed_cidr": "0.0.0.0/0"
            if changes.get("mode") == "unrestricted"
            else "203.0.113.7/32",
            "public_ip": "1.1.1.1",
            "app_url": "https://1.1.1.1",
            "auto_terminate_hours": "1",
        },
        existing={INSTANCE: "running"},
        lifecycle=lifecycle,
        public_ip=changes.get("public_ip", "1.1.1.1"),
    )
    world_path = tmp_path / "world.json"
    world_path.write_text(json.dumps(world))
    env = dict(
        os.environ,
        **aws_fakes.install(tmp_path / "bin", world_path),
        PATH=f"{tmp_path}/bin:{os.environ['PATH']}",
        AWS_PROFILE="test",
    )
    for key in ("GROUNDLINE_AWS_SCRIPT", "AWS_REGION"):
        env.pop(key, None)
    # Shorten only the test checkout's recovery budgets; production keeps 5/10 min.
    script = tmp_path / "scripts/aws-https-smoke-test"
    script.write_text(
        script.read_text()
        .replace('"$before" 300', '"$before" 2')
        .replace('"$before" 600', '"$before" 2')
    )
    for name, source in {
        "scripts/aws-smoke-test": """import json, os
from pathlib import Path
p=Path(os.environ['FAKE_AWS_WORLD']); w=json.loads(p.read_text()); l=w['lifecycle']
l['smokes']+=1; p.write_text(json.dumps(w))
failed = l.get('smoke_error') or (l.get('after_smoke_error') and l['restarts'])
raise SystemExit(1 if failed else 0)
""",
        "bin/uv": """import json, os, sys
from pathlib import Path
w=json.loads(Path(os.environ['FAKE_AWS_WORLD']).read_text()); l=w['lifecycle']
if '--validate-only' in sys.argv: raise SystemExit(0)
if l.get('tls_error') and l['restarts']: raise SystemExit(1)
fingerprint = 'new' if l.get('certificate_changed') and l['restarts'] else 'old'
print(json.dumps({'sha256':fingerprint,
                  'issuer':'test', 'not_after':'tomorrow'}))
""",
        "bin/sleep": "raise SystemExit(0)\n",
    }.items():
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
    result = subprocess.run(
        [str(script), *(["--restart", restart] if restart else [])],
        env=env,
        text=True,
        input=answer,
        capture_output=True,
        timeout=30,
    )
    return result, json.loads(world_path.read_text())


@pytest.mark.parametrize("mode", ["restricted", "unrestricted"])
def test_default_never_restarts(tmp_path: Path, mode: str):
    result, world = run(tmp_path, mode=mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert world["lifecycle"]["restarts"] == world["lifecycle"]["reboots"] == 0
    assert world["lifecycle"]["smokes"] == 1
    assert "no services were restarted" in result.stdout


@pytest.mark.parametrize("answer", ["2\n", "", "\n2\n"])
def test_cancellation_and_eof_never_restart(tmp_path: Path, answer: str):
    result, world = run(tmp_path, "host", answer)
    assert result.returncode != 0
    assert world["lifecycle"]["restarts"] == world["lifecycle"]["reboots"] == 0


@pytest.mark.parametrize("restart,smokes,reboots", [("caddy", 2, 0), ("host", 3, 1)])
def test_requested_recovery_passes(
    tmp_path: Path, restart: str, smokes: int, reboots: int
):
    result, world = run(tmp_path, restart)
    assert result.returncode == 0, result.stdout + result.stderr
    assert world["lifecycle"]["smokes"] == smokes
    assert world["lifecycle"]["restarts"] == 1
    assert world["lifecycle"]["reboots"] == reboots
    assert "same trusted certificate" in result.stdout
    assert "still running and costing money" in result.stdout


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "tester_http"},
        {"public_ip": "2.2.2.2"},
        {"ssm_error": True},
        {"deadline": 1100},
        {"smoke_error": True},
        {"mode": "unknown"},
    ],
)
def test_failed_preflight_never_restarts(tmp_path: Path, change: dict[str, Any]):
    result, world = run(tmp_path, "host", **change)
    assert result.returncode != 0
    assert world["lifecycle"]["restarts"] == world["lifecycle"]["reboots"] == 0


@pytest.mark.parametrize(
    "change,message",
    [
        ({"restart_error": True}, "Caddy restart failed"),
        ({"changed_deadline": True}, "expiry deadline changed"),
        ({"replaced_volume": True}, "storage was replaced"),
        ({"change_target": True}, "AWS target changed"),
        ({"tls_error": True}, "did not recover"),
        ({"changed_access": True}, "did not recover"),
        ({"after_smoke_error": True}, "HTTPS checks failed"),
    ],
)
def test_caddy_failure_prevents_host_reboot(
    tmp_path: Path, change: dict[str, Any], message: str
):
    result, world = run(tmp_path, "host", **change)
    assert result.returncode != 0
    assert message in result.stderr
    assert world["lifecycle"]["reboots"] == 0


@pytest.mark.parametrize(
    "change,message",
    [
        ({"unchanged_boot": True}, "did not recover"),
        ({"failed_refresh": True}, "address discovery did not complete"),
    ],
)
def test_reboot_requires_new_boot_and_successful_address_refresh(
    tmp_path: Path, change: dict[str, Any], message: str
):
    result, world = run(tmp_path, "host", **change)
    assert result.returncode != 0
    assert message in result.stderr
    assert world["lifecycle"]["smokes"] == 2  # no final smoke on unverified boot


def test_changed_valid_certificate_is_not_called_renewal_proof(tmp_path: Path):
    result, _ = run(tmp_path, "caddy", certificate_changed=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not establish why or prove renewal" in result.stdout


def test_ssm_probe_has_a_bounded_wait(tmp_path: Path):
    world_file = tmp_path / "world.json"
    world_file.write_text(
        json.dumps(
            aws_fakes.new_world(
                ssm_replies=[{"when": "diagnostic", "status": "InProgress"}]
            )
        )
    )
    env = dict(
        os.environ,
        **aws_fakes.install(tmp_path / "bin", world_file),
        PATH=f"{tmp_path}/bin:{os.environ['PATH']}",
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/aws-common.sh; region=eu-north-1; "
            "instance=i-test; on_server diagnostic 1",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "did not complete within 1 seconds" in result.stderr
    world = json.loads(world_file.read_text())
    assert not any("wait" in call for call in world["calls"])
