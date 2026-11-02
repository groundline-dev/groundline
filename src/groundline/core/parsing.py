"""Helpers for reading model replies: JSON extraction and quote matching."""

import json
import re
from typing import Any, cast

ELLIPSIS = re.compile(r"\.\.\.|…")


class ParseError(ValueError):
    """A model reply couldn't be used. The message is shown to the model on retry."""


def parse_json_object(text: str) -> dict[str, Any]:
    """The first complete JSON object in a reply, ignoring fences and any text
    before or after it."""
    decoder = json.JSONDecoder()
    first_error: json.JSONDecodeError | None = None
    start = text.find("{")
    if start == -1:
        raise ParseError("no JSON object in the reply")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError as error:
            first_error = first_error or error
        else:
            if isinstance(value, dict):
                return cast(dict[str, Any], value)  # JSON object keys are strings
        start = text.find("{", start + 1)
    raise ParseError(
        f"invalid JSON ({first_error.msg} at char {first_error.pos})"
        if first_error
        else "reply is not a JSON object"
    )


def words(text: str) -> list[str]:
    """Lower-cased alphanumeric words; punctuation and spacing ignored."""
    return re.findall(r"[a-z0-9]+", text.lower())


def evidence_in(quote: str, source: str) -> bool:
    """Does `quote` occur in `source`, word for word?

    Case, punctuation and whitespace are ignored (speech-to-text transcripts
    write "hi , ms. brooks ."). An ellipsis may join spans that occur in order.
    """
    haystack = " " + " ".join(words(source)) + " "
    parts = [words(p) for p in ELLIPSIS.split(quote)]
    parts = [p for p in parts if p]
    if not parts:
        return False
    pos = 0
    for part in parts:
        needle = " " + " ".join(part) + " "
        idx = haystack.find(needle, pos)
        if idx == -1:
            return False
        pos = idx + len(needle) - 1
    return True
