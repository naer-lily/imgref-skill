"""按 URL 缓存图片字节。

**缓存不是状态。** 删掉整个缓存目录只会让下一次变慢，不会改变任何输出——
这跟"热着的 TCP 连接"是同一级别的东西，所以它和无状态设计并不冲突。
真正会改变输出的是 ``--exclude`` 那种由调用方显式传入的状态。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Final

__all__ = ["BlobCache"]

_MB: Final = 1024 * 1024


class BlobCache:
    """内容寻址的图片缓存：``<root>/<ab>/<sha256(url)>``（无扩展名，读时嗅探）。"""

    def __init__(self, root: Path, *, cap_mb: int = 200, enabled: bool = True) -> None:
        """建立缓存句柄（不创建目录，直到第一次写入）。

        Args:
            root: 缓存根目录。
            cap_mb: 容量上限（MB），超出后按 mtime 淘汰最旧的条目。
            enabled: ``False`` 时读写都直接穿透，不落盘。
        """
        self.root = root
        self.cap_bytes = max(cap_mb, 1) * _MB
        self.enabled = enabled

    def path_for(self, url: str) -> Path:
        """返回该 URL 对应的缓存文件路径。"""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / digest

    def get(self, url: str) -> bytes | None:
        """命中则返回字节，否则返回 ``None``。"""
        if not self.enabled:
            return None
        path = self.path_for(url)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def put(self, url: str, data: bytes) -> Path | None:
        """写入缓存并返回落盘路径（未启用时返回 ``None``）。"""
        if not self.enabled:
            return None
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        if self.size_bytes() > self.cap_bytes:
            self.prune()
        return path

    def size_bytes(self) -> int:
        """当前缓存占用字节数。"""
        if not self.root.is_dir():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def prune(self) -> int:
        """按 mtime 从旧到新淘汰，直到回到容量上限内；返回删除的文件数。"""
        if not self.root.is_dir():
            return 0
        entries: list[tuple[float, int, Path]] = []
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
        entries.sort()
        total = sum(size for _, size, _ in entries)
        removed = 0
        for _, size, path in entries:
            if total <= self.cap_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def clear(self) -> int:
        """清空缓存目录，返回删除的条目数。"""
        if not self.root.is_dir():
            return 0
        entries = [p for p in self.root.rglob("*") if p.is_file()]
        shutil.rmtree(self.root, ignore_errors=True)
        return len(entries)
