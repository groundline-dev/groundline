"""scripts/aws-upgrade-smoke-test, against fake AWS, SSM, HTTPS and a stand-in
aws-deploy that changes the outputs the way an upgrade would, or wouldn't."""

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
RUNNING = "b" * 40
COMMIT = "a" * 40
"""What the stand-in git says REF is."""
KEY_ID = "0b6d5a5e-0000-4000-8000-000000000000"


def run(tmp_path: Path, ref: str = "HEAD", lifetime: str = "disposable", **broken: Any):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "terraform.tfvars").write_text(
        f'deployment_name = "alice"\nlifetime = "{lifetime}"\n'
    )
    world = aws_fakes.new_world(
        outputs={
            "instance_id": INSTANCE,
            "public_ip": "1.1.1.1",
            "app_url": "https://1.1.1.1",
            "git_ref": RUNNING,
            "lifetime": lifetime,
            "auto_terminate_hours": "0",
            "app_settings": broken.get("settings", {}),
        },
        existing={INSTANCE: "running"},
        ssm_replies=[
            {"when": "keys create", "stdout": f"ID: {KEY_ID}\nAPI key: gl_test\n"}
        ],
        snapshots=broken.get("snapshots", ["snap-1"]),
    )
    world_path = tmp_path / "world.json"
    world_path.write_text(json.dumps(world))
    env = dict(
        os.environ,
        **aws_fakes.install(tmp_path / "bin", world_path),
        PATH=f"{tmp_path}/bin:{os.environ['PATH']}",
        AWS_PROFILE="test",
        REF=ref,
        BROKEN=json.dumps(broken),
        GROUNDLINE_SMOKE_WAIT_SECONDS="0",
    )
    for key in ("GROUNDLINE_AWS_SCRIPT", "AWS_REGION"):
        env.pop(key, None)
    for name, source in {
        # An upgrade: the server runs the new commit, keeping everything else
        # unless the test breaks something.
        "scripts/aws-deploy": """import json, os, subprocess
from pathlib import Path
p = Path(os.environ['FAKE_AWS_WORLD']); w = json.loads(p.read_text())
broken = json.loads(os.environ['BROKEN'])
w['outputs']['git_ref'] = subprocess.check_output(
    ['git', 'rev-parse', os.environ['REF']], text=True).strip()
w['outputs'].update(broken.get('outputs', {}))
# A lasting upgrade to a newer commit replaces the pre-upgrade snapshot; a
# rollback keeps it.
if not broken.get('rollback'):
    w['snapshots'] = ['snap-new']
if 'snapshots_after' in broken:
    w['snapshots'] = broken['snapshots_after']
w['deployed'] = True
p.write_text(json.dumps(w))
""",
        "bin/uv": """import json, os
from pathlib import Path
w = json.loads(Path(os.environ['FAKE_AWS_WORLD']).read_text())
broken = json.loads(os.environ['BROKEN'])
new = w.get('deployed') and broken.get('certificate_changed')
print(json.dumps({'sha256': 'new' if new else 'old'}))
""",
        "bin/curl": """import json, os, sys
broken = json.loads(os.environ['BROKEN'])
url = sys.argv[-1]
if url.endswith('/openapi.json'):
    source = {'maxLength': broken.get('source_limit', 200000)}
    schema = {'properties': {'source': source}}
    print(json.dumps({'components': {'schemas': {'OmissionsRequest': schema}}}))
elif url.endswith('/v1/omissions'):
    print(401 if broken.get('lost_keys') else 422, end='')
else:
    print(200, end='')
""",
        "bin/sleep": "raise SystemExit(0)\n",
        # merge-base --is-ancestor says whether REF is older than the server's.
        "bin/git": f"""import json, os, sys
if sys.argv[1] == 'merge-base':
    raise SystemExit(0 if json.loads(os.environ['BROKEN']).get('rollback') else 1)
print('{COMMIT}')
""",
    }.items():
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
    result = subprocess.run(
        [str(tmp_path / "scripts" / "aws-upgrade-smoke-test")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    return result, json.loads(world_path.read_text())


def revoked(world: dict[str, Any]) -> bool:
    return any(f"keys revoke {KEY_ID}" in c["script"] for c in world["commands"])


def test_an_upgrade_that_keeps_everything_passes(tmp_path: Path):
    result, world = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert world["deployed"]
    for kept in (
        "the server wasn't replaced",
        "the address is still 1.1.1.1",
        "the HTTPS certificate is the same one",
        "made before the upgrade still gets past auth",
        "source length limit app_settings asks for (200000 characters)",
    ):
        assert kept in aws_fakes.flat(result.stdout)
    assert revoked(world)


def test_a_lasting_upgrade_checks_the_snapshot_was_replaced(tmp_path: Path):
    result, _ = run(tmp_path, lifetime="lasting")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "replaced the pre-upgrade snapshot with a new one (snap-new)" in result.stdout
    )


def test_a_lasting_rollback_checks_the_snapshot_was_kept(tmp_path: Path):
    result, _ = run(tmp_path, lifetime="lasting", rollback=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the rollback kept the pre-upgrade snapshot (snap-1)" in result.stdout


@pytest.mark.parametrize(
    ("broken", "says"),
    [
        ({"outputs": {"instance_id": "i-new"}}, "replaced the server"),
        ({"outputs": {"public_ip": "2.2.2.2"}}, "the address changed"),
        ({"certificate_changed": True}, "the HTTPS certificate changed"),
        ({"lost_keys": True}, "may have lost its database"),
        (
            {"source_limit": 1000, "settings": {}},
            "not the 200000 app_settings asks for",
        ),
        ({"snapshots_after": ["snap-1"], "lasting": True}, "one new pre-upgrade"),
        (
            {"snapshots_after": ["snap-1", "snap-new"], "lasting": True},
            "one new pre-upgrade",
        ),
        (
            {"snapshots_after": [], "rollback": True, "lasting": True},
            "the rollback changed the pre-upgrade snapshots",
        ),
    ],
)
def test_an_upgrade_that_loses_something_fails_and_revokes_the_key(
    tmp_path: Path, broken: dict[str, Any], says: str
):
    lifetime = "lasting" if broken.pop("lasting", False) else "disposable"

    result, world = run(tmp_path, lifetime=lifetime, **broken)

    assert result.returncode != 0
    assert says in aws_fakes.flat(result.stderr)
    assert revoked(world)


def test_the_settings_limit_is_the_one_in_app_settings(tmp_path: Path):
    result, _ = run(
        tmp_path,
        source_limit=50000,
        settings={"GROUNDLINE_MAX_SOURCE_CHARS": 50000},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "(50000 characters)" in result.stdout


def test_the_commit_has_to_differ_from_the_running_one(tmp_path: Path):
    result, world = run(tmp_path)
    world_path = tmp_path / "world.json"
    world["outputs"]["git_ref"] = COMMIT
    world.pop("deployed")
    world_path.write_text(json.dumps(world))
    rerun = subprocess.run(
        [str(tmp_path / "scripts" / "aws-upgrade-smoke-test")],
        cwd=ROOT,
        env=dict(
            os.environ,
            **aws_fakes.install(tmp_path / "bin", world_path),
            PATH=f"{tmp_path}/bin:{os.environ['PATH']}",
            AWS_PROFILE="test",
            REF="HEAD",
            BROKEN="{}",
        ),
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0
    assert rerun.returncode != 0
    assert "already runs" in rerun.stderr


def test_ref_is_required(tmp_path: Path):
    result, world = run(tmp_path, ref="")

    assert result.returncode != 0
    assert "make aws-upgrade-smoke REF=" in result.stderr
    assert not world["calls"]
