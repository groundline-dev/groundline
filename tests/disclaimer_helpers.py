"""What the tests expect of the disclaimer."""

from groundline_core._disclaimer import full_disclaimer
from groundline_core.profiles import load_builtin_profile


def clinical_disclaimer() -> str:
    """The disclaimer a check with the built-in clinical profile carries."""
    return full_disclaimer(load_builtin_profile("clinical"))


def one_line(text: str) -> str:
    """Text with its wrapping undone: every run of whitespace becomes one space,
    so text wrapped for a terminal or Markdown compares with the original."""
    return " ".join(text.split())
