import pytest

from groundline.core import StageError
from groundline.evaluators.omissions.models import RankOutput
from tests.omission_helpers import (
    EXTRACT,
    EXTRACTED,
    RANK,
    RANKED,
    Script,
    rank_reply,
    run,
)


def ranked(rank_replies: str | list[str], enabled: bool = True) -> RankOutput:
    script = Script(extract=EXTRACTED, rank=rank_replies)
    result, _ = run([EXTRACT, {**RANK, "enabled": enabled}], script)
    return result.output("rank", RankOutput)


def test_rank_is_severity_then_model_order():
    reply = rank_reply(("f3", "critical"), ("f2", "supporting"), ("f1", "critical"))
    out = ranked(reply).facts

    assert [(f.id, f.severity, f.rank) for f in out] == [
        ("f3", "critical", 1),
        ("f1", "critical", 2),
        ("f2", "supporting", 3),
    ]
    assert out[0].rationale == "because f3"
    assert out[0].fact == "Motrin 600 mg every 6 hours for a week."
    assert out[0].evidence_found is True


def test_prompt_has_the_source_facts_and_rubric():
    script = Script(extract=EXTRACTED, rank=RANKED)
    run([EXTRACT, RANK], script)

    (prompt,) = script.prompts["rank"]
    assert "it hurts more when i move it" in prompt
    assert 'f2: Pain is worse with movement.  (evidence: "it hurts more' in prompt
    assert "critical" in prompt and "peripheral" in prompt
    for placeholder in ("{transcript}", "{facts}", "{rubric}"):
        assert placeholder not in prompt


def test_severity_case_is_normalised():
    reply = rank_reply(("f1", "Critical"), ("f2", " SUPPORTING "), ("f3", "critical"))

    assert ranked(reply).facts[0].severity == "critical"


@pytest.mark.parametrize(
    "bad",
    [
        rank_reply(("f1", "critical"), ("f2", "critical")),  # f3 missing
        rank_reply(
            ("f1", "critical"),
            ("f1", "critical"),
            ("f2", "critical"),
            ("f3", "critical"),
        ),  # duplicate
        rank_reply(
            ("f1", "critical"),
            ("f2", "critical"),
            ("f3", "critical"),
            ("f9", "critical"),
        ),  # unknown id
        rank_reply(("f1", "major"), ("f2", "critical"), ("f3", "critical")),  # severity
        "not json",
    ],
)
def test_invalid_replies_retry_once_then_fail(bad: str):
    with pytest.raises(StageError, match="stage 'rank'.*after 1 retry"):
        ranked([bad, bad])


def test_invalid_reply_then_valid_reply():
    bad = rank_reply(("f1", "major"), ("f2", "critical"), ("f3", "critical"))
    script = Script(extract=EXTRACTED, rank=[bad, RANKED])
    result, _ = run([EXTRACT, RANK], script)

    assert len(script.prompts["rank"]) == 2
    assert "f1: severity is not one of" in script.prompts["rank"][1]
    assert result.output("rank", RankOutput).facts[0].id == "f3"


def test_switched_off_keeps_extraction_order_without_severity():
    script = Script(extract=EXTRACTED)
    result, _ = run([EXTRACT, {**RANK, "enabled": False}], script)

    assert "rank" not in script.prompts
    assert [
        (f.id, f.severity, f.rank) for f in result.output("rank", RankOutput).facts
    ] == [
        ("f1", None, 1),
        ("f2", None, 2),
        ("f3", None, 3),
    ]
