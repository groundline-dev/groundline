import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from groundline.core import (
    DomainProfile,
    FileCache,
    Implementation,
    InputName,
    Inputs,
    PipelineSpec,
    Placeholders,
    ProfileError,
    Registry,
    RunResult,
    SpecError,
    StageContext,
    StageError,
    StageSpec,
    build_pipeline,
    load_profile,
    run_pipeline,
)
from groundline.profiles import builtin_profiles, load_builtin_profile


class Text(BaseModel):
    value: str


class Greet(Implementation):
    """Fills its prompt with the source, using the profile's placeholder name."""

    name: ClassVar[str] = "greet.basic"
    step: ClassVar[str] = "greet"
    uses: ClassVar[tuple[InputName, ...]] = ("source",)

    prompt: str = "greet_v1"

    def profile_texts(self) -> tuple[str, ...]:
        return (self.prompt,)

    async def run(self, ctx: StageContext) -> Text:
        return Text(value=ctx.fill(ctx.text(self.prompt), extra="!"))


class Snoop(Implementation):
    """Reads a text it didn't declare."""

    name: ClassVar[str] = "greet.snoop"
    step: ClassVar[str] = "greet"

    async def run(self, ctx: StageContext) -> Text:
        return Text(value=ctx.text("greet_v1"))


def profile(**texts: str) -> DomainProfile:
    return DomainProfile(
        name="test",
        placeholders=Placeholders(source="transcript", summary="note"),
        texts=texts or {"greet_v1": "Hello {transcript}{extra} Not {note}."},
    )


def run(
    stage: dict[str, Any],
    domain: DomainProfile | None,
    cache: FileCache | None = None,
) -> RunResult:
    registry = Registry()
    registry.define_step("greet", Text)
    registry.register(Greet)
    registry.register(Snoop)
    spec = PipelineSpec(name="t", stages=[StageSpec(**stage)])
    pipeline = build_pipeline(spec, registry, profile=domain)
    inputs = Inputs(source="world", summary="secret")
    return asyncio.run(run_pipeline(pipeline, inputs, cache=cache))


GREET = {"step": "greet", "implementation": "greet.basic"}


# --- using a profile ----------------------------------------------------------


def test_fills_the_profile_placeholders_for_declared_inputs_only():
    result = run(GREET, profile())

    # {transcript} is the source; {note} is the summary, which Greet doesn't use.
    assert result.output("greet", Text).value == "Hello world! Not {note}."


def test_a_stage_using_texts_needs_a_profile():
    with pytest.raises(SpecError, match="needs a domain profile"):
        run(GREET, None)


def test_a_missing_text_is_caught_before_running():
    with pytest.raises(SpecError) as error:
        run({**GREET, "settings": {"prompt": "greet_v2"}}, profile())

    assert error.value.problems == [
        "stage 1 (greet): uses text 'greet_v2', which profile 'test' doesn't have"
    ]


def test_a_stage_cannot_read_a_text_it_did_not_declare():
    with pytest.raises(StageError, match="didn't declare it in `profile_texts`"):
        run({"step": "greet", "implementation": "greet.snoop"}, profile())


def test_editing_a_text_reruns_the_stages_that_use_it(tmp_path: Path):
    cache = FileCache(tmp_path)
    run(GREET, profile(greet_v1="Hi {transcript}"), cache)

    same = run(GREET, profile(greet_v1="Hi {transcript}"), cache)
    edited = run(GREET, profile(greet_v1="Hey {transcript}"), cache)

    assert same.stages["greet"].status == "cached"
    assert edited.stages["greet"].status == "ran"
    assert edited.output("greet", Text).value == "Hey world"


# --- loading profiles ---------------------------------------------------------


def write_profile(folder: Path, yaml: str, files: dict[str, str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.yaml").write_text(yaml)
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return folder


GOOD_YAML = """
name: finance
description: Earnings-call summaries.
placeholders: {source: call, summary: brief}
texts:
  extract_v1: prompts/extract_v1.txt
"""


def test_loads_a_profile_folder(tmp_path: Path):
    folder = write_profile(
        tmp_path / "finance", GOOD_YAML, {"prompts/extract_v1.txt": "List {call}"}
    )

    loaded = load_profile(folder)

    assert loaded.name == "finance"
    assert loaded.placeholders == Placeholders(source="call", summary="brief")
    assert loaded.texts == {"extract_v1": "List {call}"}


def test_a_missing_text_file_is_an_error(tmp_path: Path):
    folder = write_profile(tmp_path / "finance", GOOD_YAML, {})

    with pytest.raises(ProfileError, match="extract_v1.*prompts/extract_v1.txt"):
        load_profile(folder)


def test_placeholders_are_required(tmp_path: Path):
    yaml = "name: finance\ntexts: {}\n"
    folder = write_profile(tmp_path / "finance", yaml, {})

    with pytest.raises(ProfileError, match="placeholders"):
        load_profile(folder)


def test_a_folder_without_profile_yaml_is_an_error(tmp_path: Path):
    with pytest.raises(ProfileError, match="no profile.yaml"):
        load_profile(tmp_path)


# --- built-in profiles --------------------------------------------------------


def test_the_clinical_profile_is_built_in():
    clinical = load_builtin_profile("clinical")

    assert "clinical" in builtin_profiles()
    assert clinical.placeholders == Placeholders(source="transcript", summary="note")
    assert set(clinical.texts) == {
        "extract_coarse_v1",
        "rank_v1",
        "check_batch_v1",
        "refute_v1",
        "severity_v1",
        "quick_check_v1",
        "support_refute_v1",
    }
    assert "{transcript}" in clinical.texts["extract_coarse_v1"]
    assert "{note}" in clinical.texts["check_batch_v1"]


def test_an_unknown_built_in_profile_is_an_error():
    with pytest.raises(ProfileError, match="no built-in profile 'finance'"):
        load_builtin_profile("finance")
