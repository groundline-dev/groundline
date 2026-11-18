"""Built-in presets: validated pipeline specs, shipped inside the package.

Each `.yaml` file here is a preset. A preset's measured results only hold while
it and its profile are unchanged; `groundline_core._evaluate` reports whether a run
used a preset unchanged.
"""

from pathlib import Path

from groundline_core._core.spec import PipelineSpec, SpecError, load_spec

BUILTIN_DIR = Path(__file__).parent


def _builtin_files() -> dict[str, Path]:
    """Each built-in preset's file, by preset name."""
    return {p.stem: p for p in BUILTIN_DIR.glob("*.yaml")}


def builtin_presets() -> list[str]:
    return sorted(_builtin_files())


def load_preset(name: str) -> PipelineSpec:
    # The path comes from the shipped files, never from `name`, which the API
    # takes from a request.
    path = _builtin_files().get(name)
    if path is None:
        raise SpecError(
            [f"no built-in preset '{name}' (built in: {', '.join(builtin_presets())})"]
        )
    return load_spec(path)
