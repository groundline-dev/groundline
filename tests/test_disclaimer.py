"""The disclaimer's wording, and the READMEs that repeat it."""

from pathlib import Path

import pytest

from groundline_core._disclaimer import AFTER_LEAD, LEAD, SHORT, full_disclaimer
from groundline_core.profiles import load_builtin_profile

ROOT = Path(__file__).resolve().parents[1]
READMES = [ROOT / "README.md", ROOT / "packages" / "groundline-client" / "README.md"]


def one_line(text: str) -> str:
    """Markdown can wrap a paragraph anywhere, so compare text with single spaces."""
    return " ".join(text.split())


def test_a_profiles_sentence_follows_the_lead():
    assert full_disclaimer("They aren't financial advice.") == (
        f"{LEAD} They aren't financial advice. {AFTER_LEAD}"
    )


@pytest.mark.parametrize("sentence", [None, ""])
def test_without_a_profile_sentence_the_disclaimer_is_the_core(sentence: str | None):
    assert full_disclaimer(sentence) == f"{LEAD} {AFTER_LEAD}"


def test_both_readmes_carry_the_core_and_the_clinical_sentence():
    clinical = load_builtin_profile("clinical").disclaimer
    for readme in READMES:
        text = one_line(readme.read_text())
        assert f"{LEAD} {AFTER_LEAD}" in text, f"{readme} lacks the disclaimer's core"
        assert clinical in text, f"{readme} lacks the clinical sentence {clinical!r}"


def test_the_short_disclaimer_links_to_the_readmes_disclaimer_section():
    assert "https://github.com/groundline-dev/groundline#disclaimer" in SHORT
    headings = (ROOT / "README.md").read_text().splitlines()
    assert "## Disclaimer" in headings
