import json
from pathlib import Path

import pytest

from groundline.core import FileCache, StageError
from groundline.evaluators.omissions.models import ExtractOutput, Fact
from tests.fakes import CLINICAL
from tests.omission_helpers import (
    EXTRACT,
    EXTRACTED,
    SOURCE,
    Script,
    facts_reply,
    run,
)


def facts(script: Script) -> list[Fact]:
    result, _ = run([EXTRACT], script)
    return result.output("extract", ExtractOutput).facts


def test_valid_reply_becomes_facts():
    result, _ = run([EXTRACT], Script(extract=EXTRACTED))

    out = result.output("extract", ExtractOutput).facts
    assert [f.id for f in out] == ["f1", "f2", "f3"]
    assert out[1] == Fact(
        id="f2",
        fact="Pain is worse with movement.",
        evidence="it hurts more when i move it",
        evidence_found=True,
    )
    assert [c.step for c in result.calls] == ["extract"]


def test_prompt_contains_the_source_in_the_transcript_placeholder():
    script = Script(extract=EXTRACTED)
    run([EXTRACT], script)

    (prompt,) = script.prompts["extract"]
    assert SOURCE in prompt
    assert "{transcript}" not in prompt


def test_uses_opus_at_high_effort_by_default():
    _, client = run([EXTRACT], Script(extract=EXTRACTED))

    assert (client.requests[0].model, client.requests[0].effort) == (
        "claude-opus-5-5",
        "high",
    )


def test_ids_are_renumbered_in_order():
    messy = json.dumps(
        {
            "facts": [
                {"id": "f7", "fact": "a", "evidence": "i was skiing"},
                {"id": "f7", "fact": "b", "evidence": "it bent back"},
                {"fact": "c", "evidence": "motrin 600 mg"},
            ]
        }
    )
    out = facts(Script(extract=messy))

    assert [f.id for f in out] == ["f1", "f2", "f3"]
    assert [f.fact for f in out] == ["a", "b", "c"]


def test_evidence_not_in_the_source_is_flagged_not_dropped():
    reply = facts_reply(
        ("Has a fracture.", "the x-ray shows a fracture"),
        ("Was skiing.", "i was skiing"),
    )

    assert [f.evidence_found for f in facts(Script(extract=reply))] == [False, True]


def test_malformed_json_is_retried_once():
    script = Script(extract=["Sorry, here are the facts: {facts: oops", EXTRACTED])
    result, _ = run([EXTRACT], script)

    assert len(result.output("extract", ExtractOutput).facts) == 3
    assert len(script.prompts["extract"]) == 2
    assert "could not be used" in script.prompts["extract"][1]
    assert SOURCE in script.prompts["extract"][1]
    assert len(result.calls) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"facts": []}),
        json.dumps({"items": [{"fact": "a", "evidence": "b"}]}),
        json.dumps({"facts": [{"fact": "a"}]}),
        json.dumps({"facts": [{"fact": "", "evidence": "i was skiing"}]}),
        json.dumps({"facts": ["just a string"]}),
    ],
)
def test_malformed_twice_fails_clearly(bad: str):
    with pytest.raises(StageError, match="stage 'extract'.*after 1 retry"):
        run([EXTRACT], Script(extract=[bad, bad]))


def test_cached_on_rerun(tmp_path: Path):
    cache = FileCache(tmp_path)
    run([EXTRACT], Script(extract=EXTRACTED), cache=cache)

    result, client = run([EXTRACT], Script(), cache=cache)  # no replies needed

    assert result.stages["extract"].status == "cached"
    assert client.requests == []


def test_shipped_prompt_has_one_source_placeholder():
    assert CLINICAL.texts["extract_coarse_v1"].count("{transcript}") == 1
