"""文件系统工具测试。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from imgref.fsutil import link_or_copy, prune_dirs, safe_slug, unique_path


def test_link_or_copy_preserves_content(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"hello")
    dest = tmp_path / "sub" / "b.bin"
    link_or_copy(source, dest)
    assert dest.read_bytes() == b"hello"
    assert dest.parent.is_dir()


def test_link_or_copy_overwrites_existing(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"new")
    dest = tmp_path / "b.bin"
    dest.write_bytes(b"old")
    link_or_copy(source, dest)
    assert dest.read_bytes() == b"new"


def test_unique_path(tmp_path: Path) -> None:
    target = tmp_path / "x.jpg"
    assert unique_path(target) == target
    target.write_bytes(b"1")
    assert unique_path(target).name == "x-2.jpg"
    (tmp_path / "x-2.jpg").write_bytes(b"2")
    assert unique_path(target).name == "x-3.jpg"


def test_prune_dirs_keeps_newest(tmp_path: Path) -> None:
    for name in ("old", "mid", "new"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_text(name, encoding="utf-8")
        time.sleep(0.01)
    removed = prune_dirs(tmp_path, 2)
    assert [path.name for path in removed] == ["old"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["mid", "new"]


def test_prune_dirs_noop_when_keep_zero_or_missing(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert prune_dirs(tmp_path, 0) == []
    assert prune_dirs(tmp_path / "missing", 1) == []
    assert (tmp_path / "a").is_dir()


def test_prune_dirs_survives_readonly_entry(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    removed = prune_dirs(tmp_path, 1)
    assert len(removed) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_safe_slug() -> None:
    assert safe_slug("M1911 各角度!@#") == "m1911"
    assert safe_slug("   ") == "img"
    assert safe_slug("a" * 100, limit=10) == "a" * 10


def test_link_or_copy_falls_back_when_hardlink_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"payload")
    dest = tmp_path / "b.bin"

    def boom(*_: object, **__: object) -> None:
        raise OSError("no hardlinks here")

    monkeypatch.setattr(os, "link", boom)
    link_or_copy(source, dest)
    assert dest.read_bytes() == b"payload"
