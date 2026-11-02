"""An optional cache of stage outputs, so identical re-runs make no model calls.

The library only caches when it's given a cache. Cached entries hold sources,
summaries and what was extracted from them, so never cache sensitive data.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol


def stable_hash(value: Any) -> str:
    """A short hash of any JSON-serialisable value, independent of dict order."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class Cache(Protocol):
    def get(self, step: str, key: str) -> dict[str, Any] | None: ...
    def put(self, step: str, key: str, entry: dict[str, Any]) -> None: ...


class FileCache:
    """Entries stored as `<root>/<step>/<key>.json`."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, step: str, key: str) -> Path:
        return self.root / step / f"{key}.json"

    def get(self, step: str, key: str) -> dict[str, Any] | None:
        path = self._path(step, key)
        if not path.exists():
            return None
        entry: dict[str, Any] = json.loads(path.read_text())
        return entry

    def put(self, step: str, key: str, entry: dict[str, Any]) -> None:
        path = self._path(step, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
