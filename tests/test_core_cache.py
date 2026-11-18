import stat
from pathlib import Path

import pytest

from groundline_core._core import FileCache, stable_hash


def test_stores_and_returns_entries(tmp_path: Path):
    cache = FileCache(tmp_path)

    assert cache.get("extract", "abc") is None
    cache.put("extract", "abc", {"output": {"facts": []}, "calls": []})

    assert cache.get("extract", "abc") == {"output": {"facts": []}, "calls": []}
    assert (tmp_path / "extract" / "abc.json").exists()


def test_stable_hash_ignores_dict_order():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


@pytest.mark.parametrize(
    "damaged", ['{"output": {"fac', "", "[1, 2]", "\udcff not utf-8"]
)
def test_an_unreadable_entry_counts_as_a_miss(tmp_path: Path, damaged: str):
    path = tmp_path / "extract" / "abc.json"
    path.parent.mkdir()
    path.write_bytes(damaged.encode("utf-8", "surrogateescape"))

    assert FileCache(tmp_path).get("extract", "abc") is None


def test_put_replaces_the_entry_whole_and_leaves_no_temporary_files(tmp_path: Path):
    cache = FileCache(tmp_path)
    cache.put("extract", "abc", {"output": {"facts": ["old"]}, "calls": []})
    cache.put("extract", "abc", {"output": {"facts": ["new"]}, "calls": []})

    assert cache.get("extract", "abc") == {"output": {"facts": ["new"]}, "calls": []}
    assert [p.name for p in (tmp_path / "extract").iterdir()] == ["abc.json"]


def test_a_failed_write_warns_and_leaves_the_old_entry_and_no_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = FileCache(tmp_path)
    cache.put("extract", "abc", {"output": {"facts": ["old"]}, "calls": []})

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("groundline_core._core.cache.os.replace", fail)
    with pytest.warns(RuntimeWarning, match="couldn't write to the stage cache"):
        cache.put("extract", "abc", {"output": {"facts": ["new"]}, "calls": []})

    assert cache.get("extract", "abc") == {"output": {"facts": ["old"]}, "calls": []}
    assert [p.name for p in (tmp_path / "extract").iterdir()] == ["abc.json"]


def test_entries_are_private_to_their_owner(tmp_path: Path):
    root = tmp_path / "cache"
    FileCache(root).put("extract", "abc", {"output": {}, "calls": []})

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "extract").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "extract" / "abc.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "step,key", [("../x", "abc"), ("extract", "../../x"), ("a/b", "abc"), ("", "abc")]
)
def test_names_cannot_reach_outside_the_cache_folder(
    tmp_path: Path, step: str, key: str
):
    cache = FileCache(tmp_path / "cache")

    with pytest.raises(ValueError, match="not a safe cache name"):
        cache.put(step, key, {"output": {}, "calls": []})
    with pytest.raises(ValueError, match="not a safe cache name"):
        cache.get(step, key)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_write_names_neither_the_folder_nor_the_entry(tmp_path: Path):
    blocker = tmp_path / "private-folder"
    blocker.write_text("a file where the cache folder should be")
    cache = FileCache(blocker / "cache")

    with pytest.warns(RuntimeWarning) as caught:
        cache.put("extract", "abc", {"output": {"facts": []}, "calls": []})

    message = str(caught[0].message)
    assert "private-folder" not in message
    assert cache.get("extract", "abc") is None
