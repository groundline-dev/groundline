"""The disclaimer every result carries: a domain-neutral core, plus one sentence
a domain profile can add (see ADR 0021).

The README and the SDK README repeat the core word for word, and
`tests/test_disclaimer.py` checks that they still do.
"""

LEAD = "groundline's results help a person review a summary."
AFTER_LEAD = (
    "A summary that isn't flagged can still leave out or misstate important "
    "facts, so check it before relying on it. You're responsible for any decision "
    'made using groundline\'s results. groundline is provided "as is", and its '
    "authors accept no liability for outcomes from its use (see sections 7 and 8 "
    "of the Apache License 2.0)."
)
SHORT = (
    "Results need a person's review: a summary that isn't flagged can still leave "
    "out or misstate facts. See https://github.com/groundline-dev/groundline#disclaimer."
)


def full_disclaimer(profile_sentence: str | None) -> str:
    """The full disclaimer: the core, with the profile's sentence (if it has one)
    after the lead, where it reads as part of the same thought."""
    return " ".join(part for part in (LEAD, profile_sentence, AFTER_LEAD) if part)
