"""The registry: which steps exist, their output types, and their implementations.

Every implementation of a step must produce that step's output type. That's
what makes implementations interchangeable: later stages read the output the
same way, whichever implementation produced it.
"""

from pydantic import BaseModel

from groundline_core.core.implementation import Implementation


class Registry:
    def __init__(self) -> None:
        self._steps: dict[str, type[BaseModel]] = {}
        self._implementations: dict[str, type[Implementation]] = {}

    def define_step(self, step: str, output_type: type[BaseModel]) -> None:
        if step in self._steps:
            raise ValueError(f"step '{step}' is already defined")
        self._steps[step] = output_type

    def register[T: type[Implementation]](self, implementation: T) -> T:
        """Add an implementation. Returns it, so this also works as a decorator."""
        name = getattr(implementation, "name", None)
        step = getattr(implementation, "step", None)
        if not name or not step:
            raise ValueError(f"{implementation.__name__} must set `name` and `step`")
        if step not in self._steps:
            raise ValueError(f"{name}: step '{step}' is not defined")
        if name in self._implementations:
            raise ValueError(f"implementation '{name}' is already registered")
        self._implementations[name] = implementation
        return implementation

    def implementation(self, name: str) -> type[Implementation] | None:
        return self._implementations.get(name)

    def steps(self) -> list[str]:
        return list(self._steps)

    def output_type(self, step: str) -> type[BaseModel]:
        return self._steps[step]

    def implementations(self, step: str | None = None) -> list[str]:
        """Implementation names, optionally only those for one step."""
        return [
            name
            for name, impl in self._implementations.items()
            if step is None or impl.step == step
        ]
