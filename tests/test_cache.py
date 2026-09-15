"""图片缓存测试。"""

from __future__ import annotations

import time
from pathlib import Path

from imgref.cache import BlobCache


def test_put_get_round_trip(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path)
    assert cache.get("https://x/a.jpg") is None
    path = cache.put("https://x/a.jpg", b"bytes")
    assert path is not None
    assert path.read_bytes() == b"bytes"
    assert cache.get("https://x/a.jpg") == b"bytes"


def test_different_urls_do_not_collide(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path)
    cache.put("https://x/a.jpg", b"one")
    cache.put("https://x/b.jpg", b"two")
    assert cache.get("https://x/a.jpg") == b"one"
    assert cache.get("https://x/b.jpg") == b"two"


def test_disabled_cache_is_passthrough(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, enabled=False)
    assert cache.put("https://x/a.jpg", b"bytes") is None
    assert cache.get("https://x/a.jpg") is None
    assert not list(tmp_path.rglob("*"))


def test_size_and_clear(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path)
    cache.put("https://x/a.jpg", b"12345")
    assert cache.size_bytes() == 5
    assert cache.clear() == 1
    assert cache.size_bytes() == 0
    assert cache.clear() == 0


def test_prune_evicts_oldest_first(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, cap_mb=1)
    cache.cap_bytes = 6
    cache.put("https://x/a.jpg", b"aaa")
    time.sleep(0.02)
    cache.put("https://x/b.jpg", b"bbb")
    time.sleep(0.02)
    cache.put("https://x/c.jpg", b"ccc")
    assert cache.size_bytes() <= 6
    assert cache.get("https://x/a.jpg") is None
    assert cache.get("https://x/c.jpg") == b"ccc"


def test_prune_on_empty_root(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path / "missing")
    assert cache.prune() == 0
    assert cache.size_bytes() == 0
