"""Built-in presets: validated pipeline specs, shipped inside the package.

Each `.yaml` file here is a preset. A preset's measured results only hold while
it and its profile are unchanged; `groundline_core.evaluate` reports whether a run
used a preset unchanged.
"""

from pathlib import Path

from groundline_core.core.spec import PipelineSpec, load_spec

BUILTIN_DIR = Path(__file__).parent


def builtin_presets() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.yaml"))


def load_preset(name: str) -> PipelineSpec:
    if name not in builtin_presets():
        raise ValueError(
            f"no built-in preset '{name}' (built in: {', '.join(builtin_presets())})"
        )
    return load_spec(BUILTIN_DIR / f"{name}.yaml")
