"""Check the file names in docs/adr/.

Each branch picks the next free ADR number without seeing the others, so two
branches can both add the same number. That happened twice in one week (ADR
0012, ALL-70 and ALL-74), and once more when a merge brought back an old copy
beside its renumbered file (ALL-80). These tests fail when:

- two files in docs/adr/ start with the same number;
- a file's name doesn't follow NNNN-slug.md, the format AGENTS.md sets.

Hidden files such as .DS_Store are ignored: git doesn't track them.
"""

import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
ADR_NAME = re.compile(r"\d{4}-[a-z0-9]+(-[a-z0-9]+)*\.md")
"""NNNN-slug.md: four digits, then lowercase words joined by single hyphens."""
LEADING_NUMBER = re.compile(r"\d+")


def adr_problems(folder: Path) -> list[str]:
    """Every reused number and every badly named entry in `folder`, one line each."""
    by_number: defaultdict[str, list[str]] = defaultdict(list)
    naming_problems: list[str] = []
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith("."):
            continue
        if match := LEADING_NUMBER.match(entry.name):
            by_number[match.group()].append(entry.name)
        if not (entry.is_file() and ADR_NAME.fullmatch(entry.name)):
            kind = "folder" if entry.is_dir() else "file"
            naming_problems.append(
                f"{entry.name}: a {kind} not named NNNN-slug.md "
                "(four digits, then lowercase words joined by hyphens)"
            )
    reuse_problems = [
        f"{number} is used by more than one ADR: {', '.join(names)}"
        for number, names in by_number.items()
        if len(names) > 1
    ]
    return reuse_problems + naming_problems


def write_adrs(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("# A decision\n")
    return tmp_path


def test_a_clean_folder_has_no_problems(tmp_path: Path):
    folder = write_adrs(tmp_path, "0001-first.md", "0002-uses-sha-256.md")
    assert adr_problems(folder) == []


def test_two_files_with_one_number_are_named_together(tmp_path: Path):
    folder = write_adrs(
        tmp_path, "0011-keys.md", "0012-public-probes.md", "0012-postgres-login.md"
    )
    problems = adr_problems(folder)
    assert len(problems) == 1
    assert "0012-public-probes.md" in problems[0]
    assert "0012-postgres-login.md" in problems[0]


def test_three_files_with_one_number_are_named_together(tmp_path: Path):
    folder = write_adrs(tmp_path, "0005-a.md", "0005-b.md", "0005-c.md")
    problems = adr_problems(folder)
    assert len(problems) == 1
    for name in ("0005-a.md", "0005-b.md", "0005-c.md"):
        assert name in problems[0]


@pytest.mark.parametrize(
    "name",
    [
        "14-short-number.md",
        "0014_underscore.md",
        "0014-Capital.md",
        "0014-.md",
        "0014-double--hyphen.md",
        "0014-not-markdown.txt",
        "README.md",
    ],
)
def test_a_name_outside_the_format_is_named(tmp_path: Path, name: str):
    folder = write_adrs(tmp_path, "0001-first.md", name)
    problems = adr_problems(folder)
    assert len(problems) == 1
    assert name in problems[0]


def test_a_subfolder_is_named(tmp_path: Path):
    folder = write_adrs(tmp_path, "0001-first.md")
    (folder / "drafts").mkdir()
    problems = adr_problems(folder)
    assert len(problems) == 1
    assert "drafts" in problems[0]


def test_hidden_files_are_ignored(tmp_path: Path):
    folder = write_adrs(tmp_path, "0001-first.md", ".DS_Store")
    assert adr_problems(folder) == []


def test_a_badly_named_file_still_counts_toward_its_number(tmp_path: Path):
    folder = write_adrs(tmp_path, "0012-public-probes.md", "0012_postgres.md")
    problems = adr_problems(folder)
    assert len(problems) == 2
    reuse = [p for p in problems if "0012-public-probes.md" in p]
    assert len(reuse) == 1
    assert "0012_postgres.md" in reuse[0]


def test_every_problem_is_listed_at_once(tmp_path: Path):
    folder = write_adrs(
        tmp_path, "0001-a.md", "0001-b.md", "0002-c.md", "0002-d.md", "notes.md"
    )
    assert len(adr_problems(folder)) == 3


def test_docs_adr_has_no_reused_numbers_or_bad_names():
    assert ADR_DIR.is_dir(), "docs/adr/ is missing"
    problems = adr_problems(ADR_DIR)
    assert not problems, "Fix these files in docs/adr/:\n" + "\n".join(problems)
