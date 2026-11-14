from typing import ClassVar

import pytest

from groundline_core.core import Implementation, Registry, StageContext
from tests.core_fakes import Join, Text, Upper, make_registry


def test_looks_up_implementations_and_step_outputs():
    registry = make_registry()

    assert registry.implementation("join.basic") is Join
    assert registry.implementation("join.missing") is None
    assert registry.output_type("join") is Text


def test_lists_implementations_by_step():
    registry = make_registry()

    assert registry.implementations("join") == ["join.basic", "join.sneaky"]
    assert "upper.basic" in registry.implementations()


def test_a_step_can_only_be_defined_once():
    registry = Registry()
    registry.define_step("upper", Text)

    with pytest.raises(ValueError, match="already defined"):
        registry.define_step("upper", Text)


def test_implementations_need_a_defined_step():
    with pytest.raises(ValueError, match="step 'upper' is not defined"):
        Registry().register(Upper)


def test_implementation_names_are_unique():
    registry = make_registry()

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Upper)


def test_implementations_must_name_themselves():
    class Nameless(Implementation):
        step: ClassVar[str] = "upper"

        async def run(self, ctx: StageContext) -> Text:
            return Text(value="")

    registry = Registry()
    registry.define_step("upper", Text)

    with pytest.raises(ValueError, match="must set `name` and `step`"):
        registry.register(Nameless)
