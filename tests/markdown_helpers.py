"""Reading the links and headings of the repository's Markdown documents."""

import re
from collections.abc import Callable, Iterable
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


def broken_links(
    targets: Iterable[str], file_for: Callable[[str], Path | None]
) -> list[str]:
    """The targets with no file, or no heading, to go to. `file_for` gives the
    file a target points at, or None for a target the caller doesn't check;
    the part after `#` must then be one of that file's heading ids."""
    broken: list[str] = []
    for target in targets:
        file = file_for(target)
        if file is None:
            continue
        anchor = target.partition("#")[2]
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in heading_ids(file.read_text()):
            broken.append(target)
    return broken
