import json

import pytest

from groundline.core import SpecError
from groundline.evaluators.models import AlarmOutput, Severity
from groundline.evaluators.omissions.models import (
    RankedFact,
    ScoreOutput,
    Verdict,
)
from groundline.evaluators.omissions.stages.alarm import alarm_score
from groundline.evaluators.omissions.stages.score import score_verdicts
from tests.omission_helpers import (
    ALARM,
    ALL_STAGES,
    EXTRACTED,
    RANKED,
    Script,
    check_reply,
    refute_reply,
    run,
)

WEIGHTS: dict[Severity, int] = {"critical": 3, "supporting": 2, "peripheral": 1}


def fact(id: str, severity: Severity | None, rank: int, found: bool = True):
    return RankedFact(
        id=id,
        fact=f"Fact {id}.",
        evidence="e",
        evidence_found=found,
        severity=severity,
        rank=rank,
    )


FACTS = [
    fact("f3", "critical", 1),
    fact("f1", "supporting", 2, found=False),
    fact("f2", "supporting", 3),
    fact("f4", "peripheral", 4),
]
VERDICTS = [
    Verdict(fact_id="f3", label="partial", quote="Motrin", quote_found=True),
    Verdict(fact_id="f1", label="present", quote="not in note", quote_found=False),
    Verdict(fact_id="f2", label="absent"),
    Verdict(fact_id="f4", label="present", quote="x", quote_found=True),
]


# --- score ----------------------------------------------------------------------


def test_counts_and_headline_ratio():
    s = score_verdicts(FACTS, VERDICTS, None)

    assert (s.total, s.present, s.partial, s.absent) == (4, 2, 1, 1)
    assert s.missing_ratio == 0.25  # absent / total
    assert s.weighted_missing_ratio is None


def test_missing_facts_are_those_not_present_in_rank_order():
    s = score_verdicts(FACTS, VERDICTS, None)

    assert [(m.id, m.verdict) for m in s.missing_facts] == [
        ("f3", "partial"),
        ("f2", "absent"),
    ]
    first = s.missing_facts[0]
    assert (first.fact, first.severity, first.rank, first.quote) == (
        "Fact f3.",
        "critical",
        1,
        "Motrin",
    )


def test_quote_and_evidence_flags():
    s = score_verdicts(FACTS, VERDICTS, None)

    assert (s.quote_not_found, s.evidence_not_found) == (["f1"], ["f1"])


def test_weighted_ratio():
    # absent weight 2 (f2) over total 3 + 2 + 2 + 1 = 8
    assert score_verdicts(FACTS, VERDICTS, WEIGHTS).weighted_missing_ratio == 0.25


def test_weighted_with_ranking_off_weighs_every_fact_one():
    unranked = [fact(f.id, None, f.rank) for f in FACTS]
    s = score_verdicts(unranked, VERDICTS, WEIGHTS)

    assert s.weighted_missing_ratio == s.missing_ratio


def test_every_fact_needs_a_verdict():
    with pytest.raises(ValueError, match="f4"):
        score_verdicts(FACTS, VERDICTS[:3], None)


def test_no_facts_scores_zero():
    s = score_verdicts([], [], WEIGHTS)

    assert (s.total, s.missing_ratio, s.weighted_missing_ratio) == (0, 0.0, 0.0)


# --- alarm ----------------------------------------------------------------------


def test_alarm_score_uses_critical_facts_only():
    facts = [
        fact("f1", "critical", 1),
        fact("f2", "critical", 2),
        fact("f3", "supporting", 3),
    ]
    verdicts = [
        Verdict(fact_id="f1", label="partial", score=0.8),
        Verdict(fact_id="f2", label="present", score=0.1),
        Verdict(fact_id="f3", label="absent", score=0.99),
    ]
    assert alarm_score(facts, verdicts) == pytest.approx(0.4)  # partial: half weight

    verdicts[1] = Verdict(fact_id="f2", label="absent", score=0.7)
    assert alarm_score(facts, verdicts) == pytest.approx(0.7)

    only_supporting = [Verdict(fact_id="f3", label="absent", score=1.0)]
    assert alarm_score(facts, only_supporting) == 0.0


# --- the whole pipeline ------------------------------------------------------------


def run_all(
    check: str, refute: str | None = None, level: str | None = None
) -> tuple[ScoreOutput, AlarmOutput]:
    script = Script(
        extract=EXTRACTED,
        rank=RANKED,
        check=check,
        refute=refute or refute_reply(("f1", "no", None), ("f2", "no", None)),
    )
    stages = ALL_STAGES[:-1] + [
        {**ALARM, "settings": {"level": level} if level else {}}
    ]
    result, _ = run(stages, script)
    return result.output("score", ScoreOutput), result.output("alarm", AlarmOutput)


def test_pipeline_flags_a_confidently_missing_critical_fact():
    # f3 is the critical fact. The check says it's absent at 95% confidence and
    # refute doesn't find it, so the alarm reaches the strict threshold of 0.90.
    check = json.loads(check_reply({"f3": 95}))
    check["facts"][0].update(verdict="absent", quote=None)
    refute = refute_reply(("f3", "no", None), ("f1", "no", None), ("f2", "no", None))

    score, alarm = run_all(json.dumps(check), refute)

    assert (score.total, score.absent) == (3, 2)
    assert (alarm.score, alarm.level, alarm.threshold, alarm.flagged) == (
        0.95,
        "strict",
        0.90,
        True,
    )


def test_pipeline_does_not_flag_when_critical_facts_are_present():
    _, alarm = run_all(check_reply())

    assert (alarm.score, alarm.flagged) == (0.0, False)


def test_moderate_level_uses_its_own_threshold():
    _, alarm = run_all(check_reply(), level="moderate")

    assert (alarm.level, alarm.threshold) == ("moderate", 0.425)


def test_an_unknown_level_is_a_spec_error():
    with pytest.raises(SpecError, match="level 'extreme' is not one of the thresholds"):
        run_all(check_reply(), level="extreme")
