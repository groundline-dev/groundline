import pytest

from groundline_core.core import ParseError, evidence_in, parse_json_object

SOURCE = (
    "[doctor] hi , ms. brooks . how are you ?\n"
    "[patient] well , i was skiing over the weekend and it bent back .\n"
    "[doctor] does it hurt when you move it ?\n"
    "[patient] yes , it hurts more when i move it .\n"
    "[doctor] take motrin 600 mg every 6 hours for a week ."
)


def test_parse_json_object_accepts_fences_and_chatter():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here you go:\n{"a": {"b": 2}}\nDone.') == {"a": {"b": 2}}


def test_parse_json_object_ignores_text_after_the_object():
    # Seen live: Opus followed a valid verdict with extra text containing braces.
    reply = '{"verdict": "present", "quote": "x"}\n\nNote: also {see} the plan.}'
    assert parse_json_object(reply) == {"verdict": "present", "quote": "x"}
    assert parse_json_object('Use {this}: {"a": 1} {"b": 2}') == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", '{"a": ', "[1, 2]"])
def test_parse_json_object_rejects(text: str):
    with pytest.raises(ParseError):
        parse_json_object(text)


@pytest.mark.parametrize(
    "quote",
    [
        "Hi, Ms. Brooks. How are you?",  # case, punctuation, spacing differ
        "it hurts more when i move it",
        "skiing over the weekend ... it bent back",  # ellipsis joins two spans
    ],
)
def test_evidence_matching_is_tolerant_of_formatting(quote: str):
    assert evidence_in(quote, SOURCE)


@pytest.mark.parametrize(
    "quote",
    [
        "it hurts less when i move it",  # different words
        "the weekend ... skiing",  # ellipsis parts out of order
        "",
    ],
)
def test_evidence_matching_requires_the_same_words(quote: str):
    assert not evidence_in(quote, SOURCE)
