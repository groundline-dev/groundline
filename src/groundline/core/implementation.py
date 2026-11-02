"""The base class for stage implementations, and the context a stage runs in.

A *step* is a role in a pipeline, such as `extract` or `check`. An
*implementation* is one way to do a step, such as `check.opus_batch`.

Implementations are Pydantic models whose fields are their settings. That means
settings are validated (unknown ones are rejected), have defaults, and can be
described as a JSON schema for a future pipeline-builder UI, with no extra code.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from groundline.core.calls import CallRecord
from groundline.core.inputs import InputName, Inputs
from groundline.core.model_client import Effort, ModelClient
from groundline.core.profile import Placeholders
from groundline.core.templates import fill


@dataclass(frozen=True)
class StageContext:
    """What a stage can see while it runs: the inputs it declared in `uses`, the
    outputs of the steps it declared in `reads`, and the profile texts it named
    in `profile_texts` (and only those).

    Undeclared access is an error because it would make caching unsafe: a
    stage's cache key covers only what it declares.
    """

    inputs: Inputs
    step: str
    uses: tuple[InputName, ...]
    outputs: Mapping[str, BaseModel]
    texts: Mapping[str, str] = field(default_factory=dict[str, str])
    placeholders: Placeholders = Placeholders()
    client: ModelClient | None = None
    calls: list[CallRecord] = field(default_factory=list[CallRecord])

    def text(self, name: str) -> str:
        """A text from the domain profile, such as a prompt or rubric."""
        if name not in self.texts:
            raise LookupError(
                f"stage '{self.step}' used text '{name}' "
                "but didn't declare it in `profile_texts`"
            )
        return self.texts[name]

    def fill(self, template: str, **values: str) -> str:
        """Fill a profile template: the inputs this stage uses go into the
        profile's placeholders for them (e.g. the source into `{transcript}`),
        then any other `values` by name."""
        inputs = {
            getattr(self.placeholders, name): self.inputs.text(name)
            for name in self.uses
        }
        return fill(template, **inputs, **values)

    @property
    def source(self) -> str:
        return self._input("source")

    @property
    def summary(self) -> str:
        return self._input("summary")

    def _input(self, name: InputName) -> str:
        if name not in self.uses:
            raise LookupError(
                f"stage '{self.step}' read the {name} but didn't declare it in `uses`"
            )
        return self.inputs.text(name)

    async def complete(
        self, prompt: str, *, model: str, effort: Effort, max_tokens: int
    ) -> str:
        """Send a prompt to the run's model client, record the call, and return
        the reply text."""
        if self.client is None:
            raise RuntimeError(
                f"stage '{self.step}' calls a model, but the run has no model client"
            )
        started = time.perf_counter()
        reply = await self.client.complete(
            prompt, model=model, effort=effort, max_tokens=max_tokens
        )
        self.record_call(
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            seconds=round(time.perf_counter() - started, 3),
        )
        return reply.text

    def record_call(
        self, *, model: str, input_tokens: int, output_tokens: int, seconds: float = 0
    ) -> None:
        """Record a model call this stage made, for cost reports and the cache."""
        self.calls.append(
            CallRecord(
                step=self.step,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                seconds=seconds,
            )
        )

    def output[T: BaseModel](self, step: str, kind: type[T]) -> T:
        """The output of an earlier step, checked to be of the expected type."""
        if step not in self.outputs:
            raise LookupError(
                f"stage '{self.step}' read '{step}' but didn't declare it in `reads`"
            )
        value = self.outputs[step]
        if not isinstance(value, kind):
            raise TypeError(
                f"output of '{step}' is {type(value).__name__}, not {kind.__name__}"
            )
        return value


class Implementation(BaseModel, ABC):
    """One way to do a step. Subclasses set the class variables below, add their
    settings as fields, and implement `run`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ClassVar[str]
    """Unique name, `<step>.<variant>`, e.g. `check.opus_batch`."""
    step: ClassVar[str]
    """The step this implements. Its output type is defined in the registry."""
    version: ClassVar[str] = "1"
    """Bump when behaviour changes, so cached outputs stop matching."""
    reads: ClassVar[tuple[str, ...]] = ()
    """Steps whose outputs this stage needs. They must come earlier in a pipeline."""
    uses: ClassVar[tuple[InputName, ...]] = ()
    """Inputs this stage reads: "source", "summary" or both."""
    can_disable: ClassVar[bool] = False
    """Whether a pipeline may switch this stage off (it then uses `passthrough`)."""

    def profile_texts(self) -> tuple[str, ...]:
        """Names of the domain-profile texts this stage uses, usually chosen by
        its settings (e.g. `prompt: extract_coarse_v1`). A pipeline is checked
        for them before it runs, and their contents are part of the cache key."""
        return ()

    @abstractmethod
    async def run(self, ctx: StageContext) -> BaseModel:
        """Produce this step's output."""

    def passthrough(self, ctx: StageContext) -> BaseModel:
        """The output to use when the stage is switched off. Only stages with
        `can_disable = True` need this."""
        raise NotImplementedError(f"{self.name} can't be switched off")
