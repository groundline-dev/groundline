"""What the library promises to keep across upgrades: the names in
`groundline_core.__all__`, and the documented fields of the result types."""

import ast
import dataclasses
import inspect
import textwrap

from pydantic import BaseModel

import groundline_core

PUBLIC_NAMES = [
    "Alarm",
    "CommissionPreset",
    "Counts",
    "DEFAULT_COMMISSIONS_PRESET",
    "DEFAULT_PRESET",
    "EvaluationResult",
    "Finding",
    "ModelClient",
    "ModelReply",
    "PipelineSpec",
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

RESULT_TYPES = list(RESULT_FIELDS)


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
    for name in RESULT_TYPES:
        cls = getattr(groundline_core, name, None)
        if cls is None:
            undocumented.append(f"{name} (not public)")
            continue
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
