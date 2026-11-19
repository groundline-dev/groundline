"""What the library promises to keep across upgrades: the names in
`groundline_core.__all__`, and the documented fields of the result types."""

import ast
import dataclasses
import inspect
import textwrap

from pydantic import BaseModel

import groundline_core
from tests.test_errors import PUBLIC_BASES

PUBLIC_ERRORS = sorted(PUBLIC_BASES)

PUBLIC_NAMES = [
    *PUBLIC_ERRORS,
    "Alarm",
    "CommissionPreset",
    "Counts",
    "DEFAULT_COMMISSIONS_PRESET",
    "DEFAULT_PRESET",
    "EvaluationResult",
    "EvaluationType",
    "Finding",
    "ModelClient",
    "ModelReply",
    "PipelineSpec",
    "Severity",
    "Usage",
    "builtin_presets",
    "evaluate",
    "evaluate_sync",
    "load_preset",
]
"""Adding, removing or renaming a public name changes what callers can rely on,
so it has to change here too, where a reviewer sees it."""

RESULT_FIELDS = {
    "EvaluationResult": [
        "pipeline",
        "profile",
        "validated_preset",
        "evaluation_type",
        "flagged",
        "score",
        "findings",
        "counts",
        "alarm",
        "usage",
        "run",
        "warnings",
    ],
    "Finding": [
        "type",
        "severity",
        "verdict",
        "confidence",
        "statement",
        "source_evidence",
        "summary_evidence",
        "evidence_found",
        "explanation",
    ],
    "Counts": ["total", "present", "partial", "absent"],
    "Alarm": ["score", "threshold", "level"],
    "Usage": ["calls", "input_tokens", "output_tokens", "cost_usd"],
}
"""The result types' fields are promised too, so removing or renaming one has to
change here as well."""


def test_the_public_names_are_exactly_the_promised_list():
    assert sorted(groundline_core.__all__) == sorted(PUBLIC_NAMES)


def test_every_public_name_can_be_imported_from_the_package():
    missing = [name for name in PUBLIC_NAMES if not hasattr(groundline_core, name)]

    assert missing == []


def field_names(cls: type) -> list[str]:
    if dataclasses.is_dataclass(cls):
        return [f.name for f in dataclasses.fields(cls)]
    assert issubclass(cls, BaseModel), cls
    return list(cls.model_fields)


def test_the_result_types_have_exactly_the_promised_fields():
    actual = {
        name: field_names(getattr(groundline_core, name)) for name in RESULT_FIELDS
    }

    assert actual == RESULT_FIELDS


def test_every_field_of_the_result_types_has_a_docstring():
    undocumented: list[str] = []
    for name in RESULT_FIELDS:
        cls = getattr(groundline_core, name)
        body = ast.parse(textwrap.dedent(inspect.getsource(cls))).body[0]
        assert isinstance(body, ast.ClassDef), name
        statements = body.body
        for i, statement in enumerate(statements):
            if not isinstance(statement, ast.AnnAssign):
                continue
            assert isinstance(statement.target, ast.Name), name
            following = statements[i + 1] if i + 1 < len(statements) else None
            documented = (
                isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            )
            if not documented:
                undocumented.append(f"{name}.{statement.target.id}")

    assert undocumented == []


def test_every_public_error_says_when_it_is_raised():
    """A class's docstring isn't inherited, so each error needs its own."""
    undocumented: list[str] = []
    for name in PUBLIC_ERRORS:
        error = getattr(groundline_core, name, None)
        if not isinstance(error, type) or not (error.__doc__ or "").strip():
            undocumented.append(name)

    assert undocumented == []
