"""Filling prompt templates."""


def fill(template: str, **values: str) -> str:
    """Substitute `{name}` placeholders. Other braces, such as JSON examples in a
    prompt, are left alone."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template
