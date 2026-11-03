import json

import pytest

from groundline.evaluators.omissions.models import CheckOutput, RefuteOutput
from tests.omission_helpers import (
    CHECK,
    EXTRACT,
    EXTRACTED,
    RANK,
    RANKED,
    REFUTE,
    SUMMARY,
    Script,
    check_reply,
    refute_reply,
    run,
)

STAGES = [EXTRACT, RANK, CHECK, REFUTE]
# The check marks f3 present, f1 partial and f2 absent.
CLEAR_F1 = refute_reply(
    ("f1", "full", "She was skiing and hyperextended her right index finger"),
    ("f2", "no", None),
)


def refute(reply: str, enabled: bool = True) -> tuple[RefuteOutput, Script]:
    script = Script(extract=EXTRACTED, rank=RANKED, check=check_reply(), refute=reply)
    result, _ = run([EXTRACT, RANK, CHECK, {**REFUTE, "enabled": enabled}], script)
    return result.output("refute", RefuteOutput), script


def labels(out: RefuteOutput) -> dict[str, str]:
    return {v.fact_id: v.label for v in out.verdicts}


def test_refute_sees_only_flagged_facts():
    _, script = refute(CLEAR_F1)

    (prompt,) = script.prompts["refute"]
    assert SUMMARY in prompt
    assert "f1: Injured the finger skiing." in prompt
    assert "f2: Pain is worse with movement." in prompt
    assert "f3:" not in prompt  # present: not challenged


def test_refute_clears_a_flag_only_with_a_verified_quote():
    out, _ = refute(CLEAR_F1)

    assert labels(out) == {"f3": "present", "f1": "present", "f2": "absent"}
    log = {entry.id: entry for entry in out.log}
    assert log["f1"].flipped is True and log["f1"].quote_found is True
    assert log["f2"].flipped is False


@pytest.mark.parametrize(
    "found,quote,expected",
    [
        ("full", "the patient has pain on movement", "absent"),  # quote not in summary
        ("full", "Motrin", "absent"),  # too short to verify
        ("partial", "She was skiing and hyperextended", "absent"),  # only clears
        ("no", None, "absent"),
    ],
)
def test_flip_rules(found: str, quote: str | None, expected: str):
    reply = refute_reply(("f1", "no", None), ("f2", found, quote))
    out, _ = refute(reply)

    assert labels(out)["f2"] == expected


def test_refute_never_makes_a_verdict_worse():
    out, _ = refute(refute_reply(("f1", "no", None), ("f2", "no", None)))

    assert labels(out)["f1"] == "partial"


def test_with_nothing_flagged_there_is_no_call():
    all_present = json.loads(check_reply())
    for item in all_present["facts"]:
        item.update(verdict="present", quote="Motrin 600 mg every 6 hours")
    script = Script(extract=EXTRACTED, rank=RANKED, check=json.dumps(all_present))
    result, _ = run(STAGES, script)

    assert "refute" not in script.prompts
    assert result.output("refute", RefuteOutput).log == []


def test_switched_off_passes_the_check_verdicts_through():
    script = Script(extract=EXTRACTED, rank=RANKED, check=check_reply())
    result, _ = run([EXTRACT, RANK, CHECK, {**REFUTE, "enabled": False}], script)

    assert "refute" not in script.prompts
    assert (
        result.output("refute", RefuteOutput).verdicts
        == result.output("check", CheckOutput).verdicts
    )
