"""What's wrong with a spec or profile file, described without repeating its values.

YAML's and pydantic's own messages quote what they found in the file, and the
file could hold anything, a pasted key included. These keep only where the
problem is and what kind it is.
"""

import yaml
from pydantic import ValidationError


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
