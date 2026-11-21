"""Reading the links and headings of the repository's Markdown documents."""

import re
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
REPOSITORY = "https://github.com/groundline-dev/groundline"
FENCE = re.compile(r"(`{3,}|~{3,})(.*)")
"""An opening fence: three or more backticks or tildes, then the info string."""

LINK_TARGET = re.compile(
    r"\]\(([^)\s]+)"  # [text](target) or ![alt](target)
    r"|^\s*\[[^\]^][^\]]*\]:\s*<?([^\s>]+)"  # [label]: target, not a footnote
    r"|<(?:a|img)\s(?:[^>]*\s)?(?:href|src)=\"([^\"]+)\"",  # <a href>, <img src>
    re.MULTILINE,
)
"""The target of a Markdown link, image or reference, or of an HTML link or image."""


def link_targets(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in LINK_TARGET.finditer(text)]


def fences(lines: list[str]) -> list[tuple[int, int, str]]:
    """Each fenced code block in `lines`: the indexes of its opening and closing
    fences, and its info string. A block closes on a fence of the same character
    at least as long as the one that opened it."""
    found: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        opening = FENCE.match(lines[index].strip())
        if not opening:
            index += 1
            continue
        fence, info = opening.groups()
        closing = re.compile(rf"{re.escape(fence[0])}{{{len(fence)},}}")
        opened = index
        index += 1
        while index < len(lines) and not closing.fullmatch(lines[index].strip()):
            index += 1
        if index == len(lines):
            raise ValueError(f"the code block opened on line {opened + 1} never closes")
        found.append((opened, index, info.strip()))
        index += 1
    return found


def heading_ids(markdown: str) -> set[str]:
    """The ids GitHub and PyPI give the headings: lowercase, punctuation dropped,
    spaces as hyphens, and -1, -2 for repeats. A line inside a code block, such
    as a shell comment, isn't a heading."""
    lines = markdown.splitlines()
    in_code = {i for start, end, _ in fences(lines) for i in range(start, end + 1)}
    ids: set[str] = set()
    for index, line in enumerate(lines):
        if index in in_code:
            continue
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
    """The targets with no file, or no heading, to go to. `file_for` is given
    the part of a target before `#` and returns the file it points at, or None
    for a target the caller doesn't check; the part after `#` must then be one
    of that file's heading ids."""
    broken: list[str] = []
    for target in targets:
        path, _, anchor = target.partition("#")
        file = file_for(path)
        if file is None:
            continue
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in heading_ids(file.read_text()):
            broken.append(target)
    return broken
