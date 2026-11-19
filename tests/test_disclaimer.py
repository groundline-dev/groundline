"""The disclaimer's wording, and the READMEs that repeat it."""

from pathlib import Path

import pytest

from groundline_core._core import DomainProfile
from groundline_core._disclaimer import (
    AFTER_LEAD,
    LEAD,
    SHORT_DISCLAIMER,
    full_disclaimer,
)
from groundline_core.profiles import load_builtin_profile
from tests.disclaimer_helpers import one_line

ROOT = Path(__file__).resolve().parents[1]
READMES = [ROOT / "README.md", ROOT / "packages" / "groundline-client" / "README.md"]


def finance(disclaimer: str) -> DomainProfile:
    return DomainProfile(name="finance", disclaimer=disclaimer, texts={})


def test_a_profiles_disclaimer_sentence_follows_the_lead():
    assert full_disclaimer(finance("They aren't financial advice.")) == (
        f"{LEAD} They aren't financial advice. {AFTER_LEAD}"
    )


@pytest.mark.parametrize("profile", [None, finance("")], ids=["no profile", "none"])
def test_without_a_disclaimer_sentence_the_disclaimer_is_the_core(
    profile: DomainProfile | None,
):
    assert full_disclaimer(profile) == f"{LEAD} {AFTER_LEAD}"


def test_both_readmes_carry_the_core_and_the_clinical_sentence():
    clinical = load_builtin_profile("clinical").disclaimer
    for readme in READMES:
        text = one_line(readme.read_text())
        assert f"{LEAD} {AFTER_LEAD}" in text, f"{readme} lacks the disclaimer's core"
        assert clinical in text, f"{readme} lacks the clinical sentence {clinical!r}"


def test_the_short_disclaimer_links_to_the_readmes_disclaimer_section():
    assert "https://github.com/groundline-dev/groundline#disclaimer" in SHORT_DISCLAIMER
    headings = (ROOT / "README.md").read_text().splitlines()
    assert "## Disclaimer" in headings
