"""What scripts/core-install-check refuses before it installs anything: a wheel or
sdist that holds a local or secret file. The checks run on small made-up
archives, so nothing is built or installed.

Installing the real files, with pip and with uv pip, runs in CI as
`make core-check`."""

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/core-install-check"
VERSION = "0.1.0"

WHEEL_MEMBERS = [
    "groundline_core/__init__.py",
    "groundline_core/py.typed",
    f"groundline_core-{VERSION}.dist-info/METADATA",
]
SDIST_MEMBERS = [
    f"groundline_core-{VERSION}/{name}"
    for name in (
        "PKG-INFO",
        "README.md",
        "LICENSE",
        "NOTICE",
        "pyproject.toml",
        "pyproject.toml.orig",
        "src/groundline_core/__init__.py",
    )
]


def make_dist(folder: Path, *, wheel: list[str], sdist: list[str]) -> Path:
    folder.mkdir()
    with zipfile.ZipFile(
        folder / f"groundline_core-{VERSION}-py3-none-any.whl", "w"
    ) as z:
        for name in wheel:
            z.writestr(name, "")
    with tarfile.open(folder / f"groundline_core-{VERSION}.tar.gz", "w:gz") as t:
        for name in sdist:
            t.addfile(tarfile.TarInfo(name), io.BytesIO(b""))
    return folder


def check(dist: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "--dist", str(dist)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        timeout=120,
    )


@pytest.mark.parametrize(
    "extra",
    [
        "groundline_core/.env",
        "groundline_core/__pycache__/checks.cpython-313.pyc",
        "groundline_core/.ruff_cache/CACHEDIR.TAG",
        "groundline_core/checks.py.bak",
        "groundline_core/checks.py.old",
        "groundline_core/.checks.py.swp",
        "groundline_core/checks.py.orig",
        "groundline_core/.DS_Store",
    ],
)
def test_a_wheel_with_a_local_or_secret_file_is_refused(tmp_path: Path, extra: str):
    dist = make_dist(
        tmp_path / "dist", wheel=[*WHEEL_MEMBERS, extra], sdist=SDIST_MEMBERS
    )

    result = check(dist)

    assert result.returncode != 0
    assert f"unexpected: {extra}" in result.stderr, result.stderr


@pytest.mark.parametrize(
    "extra",
    [
        f"groundline_core-{VERSION}/src/groundline_core/checks.py.bak",
        f"groundline_core-{VERSION}/src/groundline_core/checks.py.orig",
        f"groundline_core-{VERSION}/src/groundline_core/.ruff_cache/CACHEDIR.TAG",
    ],
)
def test_an_sdist_with_a_local_or_secret_file_is_refused(tmp_path: Path, extra: str):
    dist = make_dist(
        tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=[*SDIST_MEMBERS, extra]
    )

    result = check(dist)

    assert result.returncode != 0
    assert f"unexpected: {extra}" in result.stderr, result.stderr


def test_the_expected_files_alone_are_not_reported_as_unexpected(tmp_path: Path):
    # The control for the two tests above: the made-up archives aren't
    # installable, so the script still fails, but not on the allow-list.
    dist = make_dist(tmp_path / "dist", wheel=WHEEL_MEMBERS, sdist=SDIST_MEMBERS)

    result = check(dist)

    assert "unexpected:" not in result.stderr, result.stderr
