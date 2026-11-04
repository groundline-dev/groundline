"""An optional cache of stage outputs, so identical re-runs make no model calls.

The library only caches when it's given a cache. Cached entries hold sources,
summaries and what was extracted from them, so never cache sensitive data.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol, cast


def stable_hash(value: Any) -> str:
    """A short hash of any JSON-serializable value, independent of dict order."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class Cache(Protocol):
    def get(self, step: str, key: str) -> dict[str, Any] | None: ...
    def put(self, step: str, key: str, entry: dict[str, Any]) -> None: ...


SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+")


class FileCache:
    """Entries stored as `<root>/<step>/<key>.json`.

    Folders it creates are readable only by their owner (0700), and so is
    every entry (0600), since entries hold sources and summaries."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, step: str, key: str) -> Path:
        for name in (step, key):
            if not SAFE_NAME.fullmatch(name):
                raise ValueError(
                    f"{name!r} is not a safe cache name: use only letters, "
                    "digits, '_' and '-'"
                )
        return self.root / step / f"{key}.json"

    def get(self, step: str, key: str) -> dict[str, Any] | None:
        """The stored entry, or None if there isn't one or it can't be read.
        The cache is only an optimization, so a damaged entry is a miss, and
        the next `put` replaces it."""
        path = self._path(step, key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cast(dict[str, Any], entry) if isinstance(entry, dict) else None

    def put(self, step: str, key: str, entry: dict[str, Any]) -> None:
        """Write to a temporary file, then rename it into place, so an
        interrupted write never leaves a half-written entry."""
        path = self._path(step, key)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        # mkstemp creates the file readable and writable by its owner only.
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(entry, file, indent=2, ensure_ascii=False)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
