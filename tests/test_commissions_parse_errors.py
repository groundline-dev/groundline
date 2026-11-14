"""Parse errors are printed and logged, so they must never repeat what the model
wrote: any value in a reply can be text copied from the source or summary."""

import json
from typing import Any

import pytest

from groundline_core.core import ParseError
from groundline_core.evaluators.commissions.stages.check import parse_commissions
from groundline_core.evaluators.commissions.stages.refute import parse_support
from tests.commissions_helpers import DOSE, DOSE_SOURCE, SUMMARY, issue
from tests.omission_helpers import SOURCE

SECRET = "patient is HIV positive and takes tenofovir"
GOOD = issue(DOSE, "contradicted", 90, "critical", DOSE_SOURCE)


def check(*items: Any) -> None:
    parse_commissions(
        json.dumps({"issues": list(items)}), summary=SUMMARY, source=SOURCE
    )


def refute(*items: Any) -> None:
    parse_support(json.dumps({"claims": list(items)}), ["q1"], source=SOURCE)


@pytest.mark.parametrize(
    "parse, message",
    [
        (lambda: check({**GOOD, "quote": SECRET}), "quote is not in the note"),
        (lambda: check({**GOOD, "quote": ""}), "quote is not in the note"),
        (lambda: check({**GOOD, "label": SECRET}), "label is not one of"),
        (lambda: check({**GOOD, "label": "supported"}), "label is not one of"),
        (lambda: check({**GOOD, "confidence": SECRET}), "confidence is not a number"),
        (lambda: check({**GOOD, "confidence": 101}), "confidence is not a number"),
        (lambda: check({**GOOD, "confidence": -1}), "confidence is not a number"),
        (lambda: check({**GOOD, "confidence": True}), "confidence is not a number"),
        (lambda: check({**GOOD, "importance": SECRET}), "importance is not one of"),
        (lambda: check(SECRET), "quote is not in the note"),
        (
            lambda: refute({"id": SECRET, "supported": False, "evidence": None}),
            "unknown, missing or repeated id",
        ),
        (
            lambda: refute(
                {"id": "q1", "supported": False, "evidence": None},
                {"id": "q1", "supported": False, "evidence": SECRET},
            ),
            "unknown, missing or repeated id",
        ),
        (lambda: refute(SECRET), "unknown, missing or repeated id"),
        (
            lambda: refute({"id": "q1", "supported": True, "evidence": SECRET}),
            "needs evidence quoted exactly",
        ),
        (
            lambda: refute({"id": "q1", "supported": True, "evidence": None}),
            "needs evidence quoted exactly",
        ),
    ],
    ids=[
        "quote not in summary",
        "empty quote",
        "unknown label",
        "supported is not a listed label",
        "confidence not a number",
        "confidence above 100",
        "confidence below 0",
        "confidence is a boolean",
        "unknown importance",
        "commission not an object",
        "refute unknown id",
        "refute repeated id",
        "refute entry not an object",
        "refute evidence not in source",
        "refute evidence missing",
    ],
)
def test_errors_name_the_problem_and_never_repeat_the_reply(parse: Any, message: str):
    with pytest.raises(ParseError, match=message) as error:
        parse()

    assert "tenofovir" not in str(error.value)


def test_a_reply_without_the_list_is_an_error():
    with pytest.raises(ParseError, match='expected an "issues" list'):
        parse_commissions('{"claims": []}', summary=SUMMARY, source=SOURCE)
    with pytest.raises(ParseError, match='expected a "claims" list'):
        parse_support('{"issues": []}', ["q1"], source=SOURCE)


def test_a_refute_reply_must_answer_every_flagged_claim():
    with pytest.raises(ParseError, match="no entry for q2"):
        parse_support(
            json.dumps({"claims": [{"id": "q1", "supported": False}]}),
            ["q1", "q2"],
            source=SOURCE,
        )
