"""Run the host address renderer with IMDS replaced at its curl boundary."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path: Path) -> Path:
    for name in ("Caddyfile", "refresh-address"):
        shutil.copy2(ROOT / "infra/https" / name, tmp_path / name)
    curl = tmp_path / "curl"
    curl.write_text("""#!/bin/sh
printf '%s\\n' "$*" >>"$CALLS"
case "$*" in
  *api/token*) echo test-token ;;
  *public-ipv4*) [ "$ADDRESS" != unavailable ] || exit 22; echo "$ADDRESS" ;;
  *) exit 1 ;;
esac
""")
    curl.chmod(0o755)
    return tmp_path


def refresh(host: Path, address: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(host / "refresh-address")],
        env=dict(
            os.environ,
            PATH=f"{host}:{os.environ['PATH']}",
            CALLS=str(host / "calls"),
            ADDRESS=address,
        ),
        text=True,
        capture_output=True,
    )


def test_address_is_replaced_before_restart_without_touching_secrets(host: Path):
    for address in ("203.0.113.7", "198.51.100.22"):
        result = refresh(host, address)
        assert result.returncode == 0, result.stderr
        config = (host / "runtime/Caddyfile").read_text()
        assert config.count(address) == 2  # site and no-SNI default
        assert "{$GROUNDLINE_PUBLIC_IP}" not in config
        assert "acme-v02.api.letsencrypt.org/directory" in config
        assert "test-token" not in result.stdout + result.stderr + config
    assert "203.0.113.7" not in config
    calls = (host / "calls").read_text()
    assert "--request PUT" in calls
    assert "X-aws-ec2-metadata-token: test-token" in calls
    assert not list((host / "runtime").glob("Caddyfile.*"))


@pytest.mark.parametrize(
    "address", ["unavailable", "", "999.1.1.1", "::1", "api.example.com", "1.2.3.4\n}"]
)
def test_bad_metadata_fails_before_proxy_startup(host: Path, address: str):
    result = refresh(host, address)
    assert result.returncode != 0
    assert not (host / "runtime/Caddyfile").exists()
