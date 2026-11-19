"""The disclaimer every result carries: a domain-neutral core, plus the
disclaimer sentence a domain profile can add (see ADR 0021).

The README and the SDK README repeat the core word for word, and
`tests/test_disclaimer.py` checks that they still do.
"""

import re

from groundline_core._core.profile import DomainProfile

LEAD = "groundline's results help a person review a summary."
AFTER_LEAD = (
    "A summary that isn't flagged can still leave out or misstate important "
    "facts, so check it before relying on it. You're responsible for any decision "
    'made using groundline\'s results. groundline is provided "as is", and its '
    "authors accept no liability for outcomes from its use (see sections 7 and 8 "
    "of the Apache License 2.0)."
)
SHORT_DISCLAIMER = (
    "Results need a person's review: a summary that isn't flagged can still leave "
    "out or misstate facts. Full disclaimer: "
    "https://github.com/groundline-dev/groundline#disclaimer"
)
"""Ends with the link, so no period can end up inside it."""

ENDS_A_SENTENCE = re.compile(r"[.!?…][\"'”’)\]]*$")
"""Final punctuation, allowing closing quotes and brackets after it."""


def full_disclaimer(profile: DomainProfile | None) -> str:
    """The disclaimer for a check that used `profile`: the core, with the
    profile's disclaimer sentence, if it has one, after the lead.

    The sentence goes inside one paragraph, so its line breaks become spaces,
    and it gets a period if it doesn't already end a sentence. A blank one counts
    as none.
    """
    sentence = " ".join(profile.disclaimer_sentence.split()) if profile else ""
    if sentence and not ENDS_A_SENTENCE.search(sentence):
        sentence += "."
    return " ".join(part for part in (LEAD, sentence, AFTER_LEAD) if part)
