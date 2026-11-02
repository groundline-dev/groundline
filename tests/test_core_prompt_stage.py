import asyncio
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from groundline.core import (
    RETRY_NOTE,
    InputName,
    Inputs,
    ModelClient,
    ParseError,
    PipelineSpec,
    PromptImplementation,
    Registry,
    RunResult,
    StageContext,
    StageError,
    StageSpec,
    build_pipeline,
    fill,
    parse_json_object,
    run_pipeline,
)
from tests.fakes import FakeClient


class Answer(BaseModel):
    value: int


def parse_answer(reply: str) -> Answer:
    value = parse_json_object(reply).get("value")
    if not isinstance(value, int):
        raise ParseError('"value" must be a whole number')
    return Answer(value=value)


class Ask(PromptImplementation):
    name: ClassVar[str] = "answer.ask"
    step: ClassVar[str] = "answer"
    uses: ClassVar[tuple[InputName, ...]] = ("source",)

    async def run(self, ctx: StageContext) -> Answer:
        prompt = fill("Count the words in {source}. Reply as JSON.", source=ctx.source)
        return await self.ask(ctx, prompt, parse_answer)


def run(
    client: ModelClient | None, settings: dict[str, Any] | None = None
) -> RunResult:
    registry = Registry()
    registry.define_step("answer", Answer)
    registry.register(Ask)
    stage = StageSpec(
        step="answer", implementation="answer.ask", settings=settings or {}
    )
    pipeline = build_pipeline(PipelineSpec(name="t", stages=[stage]), registry)
    inputs = Inputs(source="one two three", summary="")
    return asyncio.run(run_pipeline(pipeline, inputs, client=client))


def test_asks_the_model_and_parses_the_reply():
    client = FakeClient(['{"value": 3}'])

    result = run(client)

    assert result.output("answer", Answer) == Answer(value=3)
    assert (
        client.requests[0].prompt == "Count the words in one two three. Reply as JSON."
    )


def test_uses_the_stage_model_settings():
    client = FakeClient(['{"value": 3}'] * 2)

    run(client)
    run(client, {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 500})

    first, second = client.requests
    assert (first.model, first.effort, first.max_tokens) == (
        "claude-opus-5-5",
        "high",
        16000,
    )
    assert (second.model, second.effort, second.max_tokens) == (
        "claude-sonnet-5-5",
        "medium",
        500,
    )


def test_records_each_call():
    result = run(FakeClient(['{"value": 3}']))

    [call] = result.calls
    assert (call.step, call.model, call.cached) == ("answer", "claude-opus-5-5", False)
    assert call.input_tokens > 0 and call.output_tokens > 0


def test_retries_once_with_the_parse_error_appended():
    client = FakeClient(["not json", '{"value": 3}'])

    result = run(client)

    assert result.output("answer", Answer) == Answer(value=3)
    retry = client.requests[1].prompt
    assert retry == client.requests[0].prompt + RETRY_NOTE.format(
        error="no JSON object in the reply"
    )
    assert len(result.calls) == 2


def test_gives_up_after_one_retry_without_showing_the_reply():
    # A reply can quote the source, so its text stays out of error messages.
    reply = '{"value": "the patient said x"}'
    with pytest.raises(StageError, match="unusable reply after 1 retry") as error:
        run(FakeClient(['{"value": "x"}', reply]))

    assert "patient" not in str(error.value)
    assert f"the reply was {len(reply)} characters" in str(error.value)


def test_a_stage_that_calls_a_model_needs_a_client():
    with pytest.raises(StageError, match="no model client"):
        run(None)


def test_rejects_an_unknown_effort():
    from groundline.core import SpecError

    with pytest.raises(SpecError, match="setting 'effort'"):
        run(FakeClient([]), {"effort": "extreme"})


def test_fill_replaces_only_named_placeholders():
    template = 'Note: {note}\nReply like {"facts": [{"id": "f1"}]}'

    assert fill(template, note="N") == 'Note: N\nReply like {"facts": [{"id": "f1"}]}'


def test_fill_inserts_values_exactly_as_written():
    template = "<transcript>{transcript}</transcript>\n<facts>{facts}</facts>"

    filled = fill(template, transcript="They typed {facts} and {note}.", facts="f1")

    assert filled == (
        "<transcript>They typed {facts} and {note}.</transcript>\n<facts>f1</facts>"
    )
