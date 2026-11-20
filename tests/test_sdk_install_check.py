"""What scripts/sdk-install-check refuses before it installs anything: a wheel or
sdist that holds a local or secret file, or a folder that doesn't hold exactly
one build. The checks run on small made-up archives, so nothing is built or
installed.

Installing the real files, with pip and with uv pip, and building a wheel from
the sdist, run in CI as `make sdk-install-check`."""

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sdk-install-check"
VERSION = "0.1.0"

WHEEL_MEMBERS = [
    "groundline_client/__init__.py",
    "groundline_client/client.py",
    "groundline_client/py.typed",
    "groundline_client/_generated/api/v1/evaluate_omissions.py",
    f"groundline_client-{VERSION}.dist-info/METADATA",
    f"groundline_client-{VERSION}.dist-info/licenses/LICENSE",
    f"groundline_client-{VERSION}.dist-info/licenses/NOTICE",
]
SDIST_MEMBERS = [
    f"groundline_client-{VERSION}/{name}"
    for name in (
        "PKG-INFO",
        "README.md",
        "LICENSE",
        "NOTICE",
        "pyproject.toml",
        "pyproject.toml.orig",
        "src/groundline_client/__init__.py",
        "src/groundline_client/py.typed",
    )
]


def make_dist(folder: Path, *, wheel: list[str], sdist: list[str]) -> Path:
    folder.mkdir()
    with zipfile.ZipFile(
        folder / f"groundline_client-{VERSION}-py3-none-any.whl", "w"
    ) as z:
        for name in wheel:
            z.writestr(name, "")
    with tarfile.open(folder / f"groundline_client-{VERSION}.tar.gz", "w:gz") as t:
        for name in sdist:
            t.addfile(tarfile.TarInfo(name), io.BytesIO(b""))
    return folder


def check(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        timeout=120,
    )


@pytest.mark.parametrize(
    "extra",
    [
        "groundline_client/.env",
        "groundline_client/.env.local",
        "groundline_client/__pycache__/client.cpython-313.pyc",
        "groundline_client/.ruff_cache/CACHEDIR.TAG",
        "groundline_client/client.py.bak",
        "groundline_client/.DS_Store",
        f"groundline_client-{VERSION}.dist-info/.env",
        "tests/test_client.py",
    ],
)
def test_a_wheel_with_a_local_or_secret_file_is_refused(tmp_path: Path, extra: str):
    dist = make_dist(
        tmp_path / "dist", wheel=[*WHEEL_MEMBERS, extra], sdist=SDIST_MEMBERS
    )

    result = check("--dist", str(dist))

    assert result.returncode != 0
    assert f"unexpected: {extra}" in result.stderr, result.stderr


@pytest.mark.parametrize(
    "extra",
    [
        f"groundline_client-{VERSION}/.env",
        f"groundline_client-{VERSION}/.env.bak",
        f"groundline_client-{VERSION}/src/groundline_client/.env",
        f"groundline_client-{VERSION}/src/groundline_client/__pycache__/x.pyc",
        f"groundline_client-{VERSION}/src/groundline_client/client.py.orig",
        f"groundline_client-{VERSION}/tests/test_client_errors.py",
        f"groundline_client-{VERSION}/openapi.json",
        f"groundline_client-{VERSION}/generator-config.yaml",
        f"groundline_client-{VERSION}/notes.txt",
    ],
)
def test_an_sdist_with_a_local_or_secret_file_is_refused(tmp_path: Path, extra: str):
    dist = make_dist(
        tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=[*SDIST_MEMBERS, extra]
    )

    result = check("--dist", str(dist))

    assert result.returncode != 0
    assert f"unexpected: {extra}" in result.stderr, result.stderr


def test_the_expected_files_alone_are_not_reported_as_unexpected(tmp_path: Path):
    # The control for the two tests above: the made-up archives aren't
    # installable, so the script still fails, but not on the allow-list.
    dist = make_dist(tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=SDIST_MEMBERS)

    result = check("--dist", str(dist))

    assert "unexpected:" not in result.stderr, result.stderr


def test_a_folder_without_a_wheel_is_refused_and_says_what_it_holds(tmp_path: Path):
    dist = make_dist(tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=SDIST_MEMBERS)
    next(dist.glob("*.whl")).unlink()

    result = check("--dist", str(dist))

    assert result.returncode != 0
    assert "no groundline_client wheel" in result.stderr, result.stderr
    assert ".tar.gz" in result.stderr, "it should list what the folder holds"


def test_a_folder_without_an_sdist_is_refused(tmp_path: Path):
    dist = make_dist(tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=SDIST_MEMBERS)
    next(dist.glob("*.tar.gz")).unlink()

    result = check("--dist", str(dist))

    assert result.returncode != 0
    assert "no groundline_client sdist" in result.stderr, result.stderr


def test_a_folder_with_two_builds_is_refused_so_the_wrong_one_is_not_checked(
    tmp_path: Path,
):
    dist = make_dist(tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=SDIST_MEMBERS)
    (dist / "groundline_client-0.2.0-py3-none-any.whl").write_bytes(b"")

    result = check("--dist", str(dist))

    assert result.returncode != 0
    assert "more than one groundline_client build" in result.stderr, result.stderr


@pytest.mark.parametrize(
    "arguments",
    [["--nonsense"], ["--dist"], ["--from-index", "https://test.pypi.org/simple/"]],
)
def test_a_wrong_option_is_refused_with_a_usage_line(arguments: list[str]):
    result = check(*arguments)

    assert result.returncode != 0
    assert "sdk-install-check:" in result.stderr, result.stderr
