"""A scripted model client for offline tests: no network, no cost. Also a
harness that runs an evaluator's stages with the real clinical prompts and
scripted replies."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from groundline.core import (
    Effort,
    FileCache,
    Inputs,
    ModelReply,
    PipelineSpec,
    Registry,
    RunResult,
    StageSpec,
    build_pipeline,
    run_pipeline,
)
from groundline.profiles import load_builtin_profile

CLINICAL = load_builtin_profile("clinical")


@dataclass(frozen=True)
class FakeRequest:
    prompt: str
    model: str
    effort: Effort
    max_tokens: int


class FakeClient:
    """Replies with scripted text, in order, or computed from the prompt.

    Token counts are word counts. Every request is kept in `requests`.
    """

    def __init__(self, replies: Sequence[str] | Callable[[str], str]) -> None:
        self._replies = replies if callable(replies) else list(replies)
        self.requests: list[FakeRequest] = []

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> ModelReply:
        self.requests.append(FakeRequest(prompt, model, effort, max_tokens))
        if callable(self._replies):
            text = self._replies(prompt)
        else:
            assert self._replies, (
                f"FakeClient ran out of replies (call {len(self.requests)})"
            )
            text = self._replies.pop(0)
        return ModelReply(
            text=text,
            model=model,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
        )


class ScriptedReplies:
    """Scripted replies per step, given in order. Records each step's prompts.

    Use it as a `FakeClient`'s replies. Subclasses implement `step_of`, which
    says which step a prompt belongs to."""

    def __init__(self, **replies: str | Sequence[str]) -> None:
        self.replies = {
            step: [r] if isinstance(r, str) else list(r) for step, r in replies.items()
        }
        self.prompts: dict[str, list[str]] = {}

    def step_of(self, prompt: str) -> str:
        raise NotImplementedError

    def __call__(self, prompt: str) -> str:
        step = self.step_of(prompt)
        self.prompts.setdefault(step, []).append(prompt)
        queue = self.replies.get(step)
        assert queue, f"no scripted reply left for {step}"
        return queue.pop(0) if len(queue) > 1 else queue[0]


def run_stages(
    register: Callable[[Registry], None],
    stages: list[dict[str, Any]],
    script: ScriptedReplies,
    *,
    source: str,
    summary: str,
    cache: FileCache | None = None,
) -> tuple[RunResult, FakeClient]:
    """Run `stages` of the evaluator `register` defines, with the clinical
    profile and `script`'s replies."""
    registry = Registry()
    register(registry)
    spec = PipelineSpec(
        name="test", profile="clinical", stages=[StageSpec(**s) for s in stages]
    )
    pipeline = build_pipeline(spec, registry, profile=CLINICAL)
    client = FakeClient(script)
    result = asyncio.run(
        run_pipeline(
            pipeline,
            Inputs(source=source, summary=summary),
            client=client,
            cache=cache,
        )
    )
    return result, client
