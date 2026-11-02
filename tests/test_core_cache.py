from pathlib import Path

from groundline.core import FileCache, stable_hash


def test_stores_and_returns_entries(tmp_path: Path):
    cache = FileCache(tmp_path)

    assert cache.get("extract", "abc") is None
    cache.put("extract", "abc", {"output": {"facts": []}, "calls": []})

    assert cache.get("extract", "abc") == {"output": {"facts": []}, "calls": []}
    assert (tmp_path / "extract" / "abc.json").exists()


def test_stable_hash_ignores_dict_order():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})
