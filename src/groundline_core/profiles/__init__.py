"""Built-in domain profiles, shipped inside the package.

Each subfolder with a `profile.yaml` is a profile. Profiles elsewhere on disk
load the same way with `groundline_core.core.load_profile`.
"""

from pathlib import Path

from groundline_core.core.profile import DomainProfile, ProfileError, load_profile

BUILTIN_DIR = Path(__file__).parent


def builtin_profiles() -> list[str]:
    return sorted(p.parent.name for p in BUILTIN_DIR.glob("*/profile.yaml"))


def load_builtin_profile(name: str) -> DomainProfile:
    if name not in builtin_profiles():
        raise ProfileError(
            f"no built-in profile '{name}' (built in: {', '.join(builtin_profiles())})"
        )
    return load_profile(BUILTIN_DIR / name)
