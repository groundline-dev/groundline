"""Parse errors are printed and logged, so they must never repeat what the model
wrote: any value in a reply can be text copied from the source or summary."""

import json
from typing import Any

import pytest

from groundline_core.core import ParseError
from groundline_core.evaluators.omissions.models import Fact, RankedFact
from groundline_core.evaluators.omissions.stages.check import parse_batch_verdicts
from groundline_core.evaluators.omissions.stages.rank import parse_ranking
from groundline_core.evaluators.omissions.stages.refute import parse_refute

SECRET = "patient is HIV positive and takes tenofovir"
FACT = Fact(id="f1", fact="A fact.", evidence="Some evidence.")
RANKED = RankedFact(**FACT.model_dump(), severity="critical", rank=1)
VERDICT = {"id": "f1", "verdict": "present", "quote": "q", "missing_confidence": 5}


def rank(*items: Any) -> None:
    parse_ranking(json.dumps({"facts": list(items)}), [FACT])


def check(*items: Any) -> None:
    parse_batch_verdicts(json.dumps({"facts": list(items)}), [RANKED], "q")


def refute(*items: Any) -> None:
    parse_refute(json.dumps({"facts": list(items)}), ["f1"])


@pytest.mark.parametrize(
    "parse",
    [
        lambda: rank({"id": SECRET, "severity": "critical"}),
        lambda: rank({"id": "f1", "severity": SECRET}),
        lambda: check({**VERDICT, "verdict": SECRET}),
        lambda: check({**VERDICT, "missing_confidence": SECRET}),
        lambda: check(VERDICT, {**VERDICT, "id": SECRET}, {**VERDICT, "id": SECRET}),
        lambda: refute({"id": "zz", "found": "full", "quote": SECRET}),
        lambda: refute({"id": SECRET, "found": "full", "quote": "q"}),
        lambda: refute(SECRET),
    ],
    ids=[
        "rank unknown id",
        "rank severity",
        "check verdict",
        "check confidence",
        "check repeated unknown id",
        "refute entry with unknown id",
        "refute unknown id",
        "refute entry not an object",
    ],
)
def test_errors_never_repeat_the_reply(parse: Any):
    with pytest.raises(ParseError) as error:
        parse()

    assert "tenofovir" not in str(error.value)
