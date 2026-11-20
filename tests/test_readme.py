"""The READMEs are also the packages' pages on PyPI, and SECURITY.md says where to
report a vulnerability."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/groundline-dev/groundline"
SDK_README = ROOT / "packages/groundline-client/README.md"
every_pypi_readme = pytest.mark.parametrize(
    "readme", [ROOT / "README.md", SDK_README], ids=["library", "sdk"]
)

LINK_TARGET = re.compile(
    r"\]\(([^)\s]+)"  # [text](target) or ![alt](target)
    r"|^\s*\[[^\]^][^\]]*\]:\s*<?([^\s>]+)"  # [label]: target, not a footnote
    r"|<(?:a|img)\s(?:[^>]*\s)?(?:href|src)=\"([^\"]+)\"",  # <a href>, <img src>
    re.MULTILINE,
)
"""The target of a Markdown link, image or reference, or of an HTML link or image."""


def link_targets(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in LINK_TARGET.finditer(text)]


@every_pypi_readme
def test_the_readme_has_no_relative_links(readme: Path):
    # PyPI shows the README without the repository around it, so a relative
    # link there leads nowhere. Links within the page (#name) work on both: PyPI
    # rewrites them to the user-content- ids it gives headings, as GitHub does.
    targets = link_targets(readme.read_text())

    relative = [
        target
        for target in targets
        if not target.startswith(("https://", "http://", "mailto:", "#"))
    ]
    assert relative == []


def heading_ids(markdown: str) -> set[str]:
    """The ids GitHub and PyPI give the headings: lowercase, punctuation dropped,
    spaces as hyphens, and -1, -2 for repeats."""
    ids: set[str] = set()
    for line in markdown.splitlines():
        if match := re.match(r"#{1,6} (.+)", line):
            slug = re.sub(r"[^\w\- ]", "", match.group(1).lower()).replace(" ", "-")
            unique, n = slug, 0
            while unique in ids:
                n += 1
                unique = f"{slug}-{n}"
            ids.add(unique)
    return ids


@every_pypi_readme
def test_each_link_within_the_readme_has_a_heading_to_go_to(readme: Path):
    text = readme.read_text()
    ids = heading_ids(text)

    missing = [
        target
        for target in link_targets(text)
        if target.startswith("#")
        if target[1:] not in ids
    ]
    assert missing == []


@pytest.mark.parametrize(
    ("readme", "install"),
    [
        (ROOT / "README.md", "pip install groundline-core"),
        (SDK_README, "pip install groundline-client"),
    ],
    ids=["library", "sdk"],
)
def test_the_readme_says_to_install_from_pypi(readme: Path, install: str):
    text = readme.read_text()

    assert install in text
    assert "isn't on PyPI yet" not in text


def test_security_reports_go_to_the_shared_address_privately():
    security = (ROOT / "SECURITY.md").read_text()

    assert "groundlinedevs@gmail.com" in security
    assert f"{REPOSITORY}/blob/main/SECURITY.md" in link_targets(
        (ROOT / "README.md").read_text()
    ), "the README doesn't link to SECURITY.md"


@every_pypi_readme
def test_each_link_into_the_repository_has_a_file_and_heading_to_go_to(readme: Path):
    # The README's links into the repository are absolute and pinned to main,
    # so a moved file or a renamed heading would break them silently.
    prefix = f"{REPOSITORY}/blob/main/"
    broken: list[str] = []
    for target in link_targets(readme.read_text()):
        if not target.startswith(prefix):
            continue
        path, _, anchor = target.removeprefix(prefix).partition("#")
        file = ROOT / path
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in heading_ids(file.read_text()):
            broken.append(target)
    assert broken == []
