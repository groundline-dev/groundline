"""infra/server/apply, run in a spare checkout with stand-in curl (for the
instance metadata service), aws, docker and git, so nothing leaves this computer.

It runs on the server on its first boot and on every upgrade, so these check
what has to hold across runs: the database passwords are made once and kept on
the data volume, and the Anthropic API key is read again each time.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPLY = ROOT / "infra" / "server" / "apply"

FAKES = {
    # IMDSv2: a token, then the metadata paths apply reads.
    "curl": """#!/bin/sh
for arg; do last="$arg"; done
case "$last" in
  */api/token) echo token ;;
  */meta-data/placement/region) echo eu-north-1 ;;
  */meta-data/tags/instance/deployment) echo alice ;;
  */meta-data/tags/instance)
    # Tags reach the metadata late: after SWITCH_AFTER reads of the list, it
    # shows SETTING_TAGS_LATER in place of SETTING_TAGS.
    echo read >>"$TAG_READS"
    tags="$SETTING_TAGS"
    reads="$(wc -l <"$TAG_READS")"
    if [ -n "${SWITCH_AFTER:-}" ] && [ "$reads" -gt "$SWITCH_AFTER" ]; then
      tags="$SETTING_TAGS_LATER"
    fi
    printf 'Name\ndeployment\n%s' "$tags" ;;
  */groundline-setting-GROUNDLINE_CHECK_TIMEOUT_SECONDS) echo 180.5 ;;
  */groundline-setting-GROUNDLINE_MAX_BODY_BYTES) echo 1048576 ;;
  */groundline-setting-GROUNDLINE_MODEL_MAX_ATTEMPTS) echo '5; rm' ;;
  *) echo "fake curl: unexpected $last" >&2; exit 22 ;;
esac
""",
    "aws": """#!/bin/sh
echo "aws $*" >>"$CALLS"
case "$FAKE_PARAMETER" in
  missing) echo 'An error occurred (ParameterNotFound)' >&2; exit 254 ;;
  denied) echo 'An error occurred (AccessDeniedException)' >&2; exit 254 ;;
  *) echo "$FAKE_PARAMETER" ;;
esac
""",
    "docker": '#!/bin/sh\necho "docker $*" >>"$CALLS"\n',
    "git": "#!/bin/sh\necho aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
}


@pytest.fixture
def server(tmp_path: Path) -> Path:
    """A checkout with infra/server/apply and a stand-in refresh-address."""
    checkout = tmp_path / "opt" / "groundline"
    (checkout / "infra" / "server").mkdir(parents=True)
    shutil.copy2(APPLY, checkout / "infra" / "server" / "apply")
    refresh = checkout / "infra" / "https" / "refresh-address"
    refresh.parent.mkdir(parents=True)
    refresh.write_text('#!/bin/sh\necho refresh-address >>"$CALLS"\n')
    refresh.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in FAKES.items():
        (bin_dir / name).write_text(source)
        (bin_dir / name).chmod(0o755)
    return checkout


def apply(
    server: Path,
    parameter: str = "sk-ant-test",
    setting_tags: str = "",
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    """Run apply; EXTRA sets more environment, such as the expected settings."""
    tmp_path = server.parents[1]
    reads = tmp_path / "tag-reads"
    reads.write_text("")
    env = dict(
        os.environ,
        PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        CALLS=str(tmp_path / "calls"),
        FAKE_PARAMETER=parameter,
        SETTING_TAGS=setting_tags,
        TAG_READS=str(reads),
        GROUNDLINE_DATA_DIR=str(tmp_path / "srv" / "groundline-data"),
        GROUNDLINE_SETTINGS_WAIT_SECONDS="0",
        **extra,
    )
    # Without it, apply reads the tags once, as on a first boot.
    if "GROUNDLINE_EXPECTED_SETTINGS" not in extra:
        env.pop("GROUNDLINE_EXPECTED_SETTINGS", None)
    return subprocess.run(
        [str(server / "infra" / "server" / "apply")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def secrets(server: Path) -> Path:
    return server.parents[1] / "srv" / "groundline-data" / "secrets" / "db.env"


def env_lines(server: Path) -> list[str]:
    return (server / ".env").read_text().splitlines()


def test_the_first_run_makes_passwords_on_the_data_volume_and_starts_the_stack(
    server: Path,
):
    result = apply(server)

    assert result.returncode == 0, result.stderr
    saved = secrets(server).read_text().splitlines()
    assert [line.split("=")[0] for line in saved] == [
        "POSTGRES_PASSWORD",
        "GROUNDLINE_DB_PASSWORD",
    ]
    for line in saved:
        assert len(line.split("=")[1]) == 48 and int(line.split("=")[1], 16) >= 0
    assert env_lines(server) == [
        *saved,
        "COMPOSE_FILE=compose.yaml:compose.https.yaml",
        "ANTHROPIC_API_KEY=sk-ant-test",
    ]
    for path in (secrets(server), server / ".env"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    calls = (server.parents[1] / "calls").read_text().splitlines()
    assert "refresh-address" in calls
    assert any("compose up --detach --build" in call for call in calls)
    assert calls.index("refresh-address") < next(
        i for i, call in enumerate(calls) if "compose up" in call
    )
    parameter = "--name /groundline/alice/anthropic-api-key"
    assert any(
        "ssm get-parameter --region eu-north-1" in call
        and parameter in call
        and "--with-decryption" in call
        for call in calls
    )
    assert "running aaaaaaa" in result.stdout


def test_later_runs_keep_the_passwords_and_read_the_key_again(server: Path):
    assert apply(server, "sk-ant-old").returncode == 0
    first = secrets(server).read_text()

    result = apply(server, "sk-ant-new")

    assert result.returncode == 0, result.stderr
    assert secrets(server).read_text() == first
    assert "ANTHROPIC_API_KEY=sk-ant-new" in env_lines(server)
    assert "made the database passwords" not in result.stdout


def test_a_missing_key_starts_the_app_without_one(server: Path):
    result = apply(server, "missing")

    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("ANTHROPIC_API_KEY") for line in env_lines(server))
    assert "doesn't exist" in result.stdout


def test_any_other_parameter_error_stops_before_changing_env(server: Path):
    assert apply(server, "sk-ant-old").returncode == 0
    before = (server / ".env").read_text()

    result = apply(server, "denied")

    assert result.returncode != 0
    assert "AccessDeniedException" in result.stderr
    assert (server / ".env").read_text() == before
    assert not (server / ".env.new").exists()


def test_setting_tags_become_env_lines_and_untagged_settings_use_defaults(
    server: Path,
):
    tags = (
        "groundline-setting-GROUNDLINE_CHECK_TIMEOUT_SECONDS\n"
        "groundline-setting-GROUNDLINE_MAX_BODY_BYTES\n"
        "groundline-access-mode\n"
    )

    result = apply(server, setting_tags=tags)

    assert result.returncode == 0, result.stderr
    lines = env_lines(server)
    assert lines[-2:] == [
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS=180.5",
        "GROUNDLINE_MAX_BODY_BYTES=1048576",
    ]
    assert not any(line.startswith("GROUNDLINE_MODEL") for line in lines)

    # Removing a tag removes the setting, so the app goes back to its default.
    assert apply(server).returncode == 0
    assert not any(
        line.startswith(("GROUNDLINE_CHECK", "GROUNDLINE_MAX"))
        for line in env_lines(server)
    )


def test_a_setting_tag_that_isnt_a_number_stops_before_changing_env(server: Path):
    assert apply(server).returncode == 0
    before = (server / ".env").read_text()

    result = apply(
        server, setting_tags="groundline-setting-GROUNDLINE_MODEL_MAX_ATTEMPTS\n"
    )

    assert result.returncode != 0
    assert "isn't a number" in result.stderr
    assert (server / ".env").read_text() == before


TIMEOUT = "groundline-setting-GROUNDLINE_CHECK_TIMEOUT_SECONDS\n"
BODY = "groundline-setting-GROUNDLINE_MAX_BODY_BYTES\n"


def test_an_upgrade_waits_for_the_settings_terraform_tagged(server: Path):
    # The tags still show the old settings for the first two reads.
    result = apply(
        server,
        setting_tags="",
        SETTING_TAGS_LATER=TIMEOUT + BODY,
        SWITCH_AFTER="2",
        GROUNDLINE_EXPECTED_SETTINGS=(
            "GROUNDLINE_CHECK_TIMEOUT_SECONDS=180.5 GROUNDLINE_MAX_BODY_BYTES=1048576"
        ),
    )

    assert result.returncode == 0, result.stderr
    assert env_lines(server)[-2:] == [
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS=180.5",
        "GROUNDLINE_MAX_BODY_BYTES=1048576",
    ]


def test_an_upgrade_waits_for_a_removed_setting_to_go(server: Path):
    result = apply(
        server,
        setting_tags=TIMEOUT + BODY,
        SETTING_TAGS_LATER=TIMEOUT,
        SWITCH_AFTER="1",
        GROUNDLINE_EXPECTED_SETTINGS="GROUNDLINE_CHECK_TIMEOUT_SECONDS=180.5",
    )

    assert result.returncode == 0, result.stderr
    lines = env_lines(server)
    assert "GROUNDLINE_CHECK_TIMEOUT_SECONDS=180.5" in lines
    assert not any(line.startswith("GROUNDLINE_MAX_BODY") for line in lines)


def test_an_upgrade_with_no_settings_waits_for_none(server: Path):
    result = apply(
        server,
        setting_tags=BODY,
        SETTING_TAGS_LATER="",
        SWITCH_AFTER="1",
        GROUNDLINE_EXPECTED_SETTINGS="",
    )

    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("GROUNDLINE_MAX") for line in env_lines(server))


def test_settings_that_never_arrive_stop_before_changing_env(server: Path):
    assert apply(server).returncode == 0
    before = (server / ".env").read_text()

    result = apply(
        server,
        setting_tags="",
        GROUNDLINE_EXPECTED_SETTINGS="GROUNDLINE_MAX_BODY_BYTES=1048576",
        GROUNDLINE_SETTINGS_ATTEMPTS="2",
    )

    assert result.returncode != 0
    assert 'show "", not "GROUNDLINE_MAX_BODY_BYTES=1048576"' in result.stderr
    assert (server / ".env").read_text() == before


def test_the_first_boot_reads_the_tags_once(server: Path):
    result = apply(server, setting_tags=BODY, SETTING_TAGS_LATER="", SWITCH_AFTER="1")

    assert result.returncode == 0, result.stderr
    assert "GROUNDLINE_MAX_BODY_BYTES=1048576" in env_lines(server)
    tag_reads = server.parents[1] / "tag-reads"
    assert tag_reads.read_text().count("read") == 1
