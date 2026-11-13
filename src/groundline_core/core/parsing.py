"""Helpers for reading model replies: JSON extraction and quote matching."""

import json
import re
from collections.abc import Mapping
from typing import Any, cast

ELLIPSIS = re.compile(r"\.\.\.|…")


class ParseError(ValueError):
    """A model reply couldn't be used. The message is shown to the model on retry,
    and printed if the retry fails too, so it must never repeat a value from the
    reply: any of them could be text from the source or summary. Name the field,
    and the fact id only if it's one of ours."""


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


def get_list(obj: dict[str, Any], key: str) -> list[Any] | None:
    """`obj[key]` if it's a JSON array, else None."""
    value = obj.get(key)
    return cast(list[Any], value) if isinstance(value, list) else None


def as_object(value: Any) -> dict[str, Any] | None:
    """`value` if it's a JSON object, else None."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def reply_list(reply: str, key: str) -> list[Any]:
    """The `key` array of the reply's JSON object."""
    items = get_list(parse_json_object(reply), key)
    if items is None:
        article = "an" if key[0] in "aeiou" else "a"
        raise ParseError(f'expected {article} "{key}" list')
    return items


def choice[T](item: dict[str, Any], key: str, options: Mapping[str, T]) -> T | None:
    """`item[key]` looked up in `options`, ignoring case and surrounding spaces;
    None if it isn't one of them."""
    return options.get(str(item.get(key, "")).strip().lower())


def stripped(value: Any) -> str | None:
    """`value` without surrounding spaces, if it's a string with any text in it;
    else None."""
    return (value.strip() or None) if isinstance(value, str) else None


def percent(item: dict[str, Any], key: str, where: str) -> float:
    """`item[key]`, a number from 0 to 100, as a fraction rounded to 4 places."""
    value = item.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 100
    ):
        raise ParseError(f"{where}: {key} is not a number from 0 to 100")
    return round(value / 100, 4)


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
