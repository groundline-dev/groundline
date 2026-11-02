"""Filling prompt templates."""

import re


def fill(template: str, **values: str) -> str:
    """Substitute `{name}` placeholders. Other braces, such as JSON examples in a
    prompt, are left alone.

    Every placeholder is replaced in one pass, so the values go in exactly as
    written: a `{facts}` inside a transcript is never filled in itself."""
    if not values:
        return template
    pattern = "|".join(re.escape(name) for name in values)
    return re.sub(
        r"\{(" + pattern + r")\}", lambda match: values[match.group(1)], template
    )
