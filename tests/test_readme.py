"""The README is also the package's page on PyPI, and SECURITY.md says where to
report a vulnerability."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/groundline-dev/groundline"

LINK_TARGET = re.compile(
    r"\]\(([^)\s]+)"  # [text](target) or ![alt](target)
    r"|^\s*\[[^\]^][^\]]*\]:\s*<?([^\s>]+)"  # [label]: target, not a footnote
    r"|<(?:a|img)\s(?:[^>]*\s)?(?:href|src)=\"([^\"]+)\"",  # <a href>, <img src>
    re.MULTILINE,
)
"""The target of a Markdown link, image or reference, or of an HTML link or image."""


def link_targets(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in LINK_TARGET.finditer(text)]


def test_the_readme_has_no_relative_links():
    # PyPI shows the README without the repository around it, so a relative
    # link there leads nowhere. Links within the page (#name) work on both: PyPI
    # rewrites them to the user-content- ids it gives headings, as GitHub does.
    targets = link_targets((ROOT / "README.md").read_text())

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


def test_each_link_within_the_readme_has_a_heading_to_go_to():
    text = (ROOT / "README.md").read_text()
    ids = heading_ids(text)

    missing = [
        target
        for target in link_targets(text)
        if target.startswith("#")
        if target[1:] not in ids
    ]
    assert missing == []


def test_the_readme_says_to_install_from_pypi():
    text = (ROOT / "README.md").read_text()

    assert "pip install groundline-core" in text
    assert "isn't on PyPI yet" not in text


def test_security_reports_go_to_the_shared_address_privately():
    security = (ROOT / "SECURITY.md").read_text()

    assert "groundlinedevs@gmail.com" in security
    assert f"{REPOSITORY}/blob/main/SECURITY.md" in link_targets(
        (ROOT / "README.md").read_text()
    ), "the README doesn't link to SECURITY.md"
