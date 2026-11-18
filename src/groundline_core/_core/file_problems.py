"""What's wrong with a file, described without its folders or its values.

An OSError's text repeats the full path, and YAML's and pydantic's messages
quote what they found in the file, which could hold anything, a pasted key
included. These keep only which file, where in it, and what kind of problem.
"""

from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError


def os_reason(error: OSError) -> str:
    """Why a file operation failed, such as "Permission denied", without the
    path that `str(error)` would repeat."""
    return error.strerror or type(error).__name__


def read_text(path: Path, error: Callable[[str], Exception]) -> str:
    """The file's text, or `error(problem)` raised with a problem that names the
    file by its name only. Raised `from None`: the original's text has the
    full path."""
    try:
        return path.read_text()
    except UnicodeDecodeError:
        raise error(f"{path.name} isn't readable UTF-8 text") from None
    except OSError as failure:
        raise error(f"can't read {path.name} ({os_reason(failure)})") from None


def file_problems(error: yaml.YAMLError | ValidationError) -> list[str]:
    """One line per problem: a field's location and pydantic's message for
    it, or the line and column of invalid YAML."""
    if isinstance(error, ValidationError):
        problems: list[str] = []
        for problem in error.errors(include_input=False, include_url=False):
            where = ".".join(str(part) for part in problem["loc"])
            problems.append(f"{where}: {problem['msg']}" if where else problem["msg"])
        return problems
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return ["invalid YAML"]
    return [f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}"]
