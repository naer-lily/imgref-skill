"""文件系统小工具：硬链接优先、目录轮转、文件名去重。"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Final

__all__ = ["link_or_copy", "prune_dirs", "safe_slug", "unique_path"]

_UNSAFE: Final = re.compile(r"[^A-Za-z0-9._-]+")


def link_or_copy(src: Path, dst: Path) -> None:
    """把 ``src`` 放到 ``dst``：同卷优先硬链接（不占第二份磁盘），否则复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def unique_path(path: Path) -> Path:
    """若路径已存在，追加 ``-2``、``-3`` …（保留原扩展名）。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"无法为 {path} 找到可用文件名")


def prune_dirs(root: Path, keep: int) -> list[Path]:
    """只保留 ``root`` 下最新的 ``keep`` 个子目录（按 mtime），返回被删列表。"""
    if keep <= 0 or not root.is_dir():
        return []
    children = [child for child in root.iterdir() if child.is_dir()]
    if len(children) <= keep:
        return []
    children.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for stale in children[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
        removed.append(stale)
    return removed


def safe_slug(text: str, *, limit: int = 48) -> str:
    """把任意字符串变成安全的文件名片段。"""
    slug = _UNSAFE.sub("-", text.strip()).strip("-._")
    return (slug[:limit] or "img").lower()
