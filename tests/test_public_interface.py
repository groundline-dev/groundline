"""What the library promises to keep across upgrades: the names in
`groundline_core.__all__`, and the documented fields of the result types."""

import ast
import inspect
import textwrap

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

RESULT_TYPES = ["EvaluationResult", "Finding", "Counts", "Alarm", "Usage"]


def test_the_public_names_are_exactly_the_promised_list():
    assert sorted(groundline_core.__all__) == sorted(PUBLIC_NAMES)


def test_every_public_name_can_be_imported_from_the_package():
    missing = [name for name in PUBLIC_NAMES if not hasattr(groundline_core, name)]

    assert missing == []


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
