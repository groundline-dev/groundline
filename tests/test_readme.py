"""The README is also the package's page on PyPI, and SECURITY.md says where to
report a vulnerability."""

from pathlib import Path

from tests.markdown_helpers import README, REPOSITORY, ROOT, broken_links, link_targets


def test_the_readme_has_no_relative_links():
    # PyPI shows the README without the repository around it, so a relative
    # link there leads nowhere. Links within the page (#name) work on both: PyPI
    # rewrites them to the user-content- ids it gives headings, as GitHub does.
    targets = link_targets(README.read_text())

    relative = [
        target
        for target in targets
        if not target.startswith(("https://", "http://", "mailto:", "#"))
    ]
    assert relative == []


def test_each_link_within_the_readme_has_a_heading_to_go_to():
    targets = link_targets(README.read_text())

    assert broken_links(targets, lambda path: None if path else README) == []


def test_the_readme_says_to_install_from_pypi():
    text = README.read_text()

    assert "pip install groundline-core" in text
    assert "isn't on PyPI yet" not in text


def test_security_reports_go_to_the_shared_address_privately():
    security = (ROOT / "SECURITY.md").read_text()

    assert "groundlinedevs@gmail.com" in security
    assert f"{REPOSITORY}/blob/main/SECURITY.md" in link_targets(README.read_text()), (
        "the README doesn't link to SECURITY.md"
    )


def test_each_link_into_the_repository_has_a_file_and_heading_to_go_to():
    # The README's links into the repository are absolute and pinned to main,
    # so a moved file or a renamed heading would break them silently.
    prefix = f"{REPOSITORY}/blob/main/"

    def file_for(path: str) -> Path | None:
        return ROOT / path.removeprefix(prefix) if path.startswith(prefix) else None

    assert broken_links(link_targets(README.read_text()), file_for) == []
