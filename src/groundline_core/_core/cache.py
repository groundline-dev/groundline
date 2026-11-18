"""An optional cache of stage outputs, so identical re-runs make no model calls.

The library only caches when it's given a cache. Cached entries hold sources,
summaries and what was extracted from them, so never cache sensitive data.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol, cast

from groundline_core._core.file_problems import os_reason

logger = logging.getLogger("groundline.cache")


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
        self._write_failed = False

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
        interrupted write never leaves a half-written entry.

        The cache is only an optimization, so a write that fails (a full disk,
        a folder that can't be written) leaves the cache as it was, rather
        than failing a check whose model calls are already paid for. The first
        failure is logged as a warning; later ones would only repeat it.

        It's a log record, not a Python warning, because a warnings filter set
        to "error" would turn a warning into an exception that fails the check."""
        path = self._path(step, key)
        try:
            self._write(path, key, entry)
        except OSError as error:
            if not self._write_failed:
                self._write_failed = True
                logger.warning(
                    "couldn't write to the stage cache (%s); "
                    "this run's results won't be cached",
                    os_reason(error),
                )

    def _write(self, path: Path, key: str, entry: dict[str, Any]) -> None:
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
