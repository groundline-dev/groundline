"""The base class for stage implementations, and the context a stage runs in.

A *step* is a role in a pipeline, such as `extract` or `check`. An
*implementation* is one way to do a step, such as `check.opus_batch`.

Implementations are Pydantic models whose fields are their settings. That means
settings are validated (unknown ones are rejected), have defaults, and can be
described as a JSON schema for a future pipeline-builder UI, with no extra code.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from groundline.core.inputs import Inputs


@dataclass(frozen=True)
class StageContext:
    """What a stage can see while it runs: the inputs, and the outputs of the
    steps it declared in `reads` (and only those)."""

    inputs: Inputs
    step: str
    outputs: Mapping[str, BaseModel]

    def output[T: BaseModel](self, step: str, kind: type[T]) -> T:
        """The output of an earlier step, checked to be of the expected type."""
        if step not in self.outputs:
            # Undeclared reads would make caching unsafe: a stage's cache key
            # covers only the steps it declares.
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
    can_disable: ClassVar[bool] = False
    """Whether a pipeline may switch this stage off (it then uses `passthrough`)."""

    @abstractmethod
    async def run(self, ctx: StageContext) -> BaseModel:
        """Produce this step's output."""

    def passthrough(self, ctx: StageContext) -> BaseModel:
        """The output to use when the stage is switched off. Only stages with
        `can_disable = True` need this."""
        raise NotImplementedError(f"{self.name} can't be switched off")
