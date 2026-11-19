"""Built-in domain profiles, shipped inside the package.

Each subfolder with a `profile.yaml` is a profile. Profiles elsewhere on disk
load the same way with `groundline_core._core.load_profile`.
"""

from pathlib import Path

from groundline_core._core.profile import DomainProfile, ProfileError, load_profile

BUILTIN_DIR = Path(__file__).parent


def _builtin_folders() -> dict[str, Path]:
    """Each built-in profile's folder, by profile name."""
    return {p.parent.name: p.parent for p in BUILTIN_DIR.glob("*/profile.yaml")}


def builtin_profiles() -> list[str]:
    return sorted(_builtin_folders())


def load_builtin_profile(name: str) -> DomainProfile:
    # The path comes from the shipped folders, never from `name`, which can come
    # from a spec.
    folder = _builtin_folders().get(name)
    if folder is None:
        raise ProfileError(
            f"no built-in profile '{name}' (built in: {', '.join(builtin_profiles())})"
        )
    return load_profile(folder)
