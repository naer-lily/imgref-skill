"""manifest（``results.json``）与 ``--exclude`` 集合。

manifest 是**副产品，不是必需品**：ID 自包含，所以 ``preview`` / ``download``
完全不依赖它。它存在的理由是溯源（来源页、尺寸、许可）和三件可选便利：
``--pick … --from``、``montage``、``--exclude``。

``--exclude`` 排除的依据是 **aHash 而不是 ID**：同一张图在缩略图/原图、不同协议、
不同图源下的 URL 各不相同，按 ID 根本排除不掉；而换个图源重搜时 ID 更是完全对不上。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from imgref.errors import ImgrefError, UsageError
from imgref.grid import GridSpec
from imgref.types import ImageResult
from imgref.urlutil import normalize_url

__all__ = ["SCHEMA_VERSION", "Cell", "ExcludeSet", "load_exclude", "read_cells", "write_manifest"]

SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class Cell:
    """拼图里的一格 + 它的溯源信息。"""

    ordinal: int
    image: ImageResult
    ahash: str | None = None
    thumb_file: str | None = None
    local: str | None = None

    @property
    def ref_id(self) -> str:
        """该格的自包含 ID。"""
        return self.image.ref_id


@dataclass(frozen=True, slots=True)
class ExcludeSet:
    """跨轮排除集合：aHash 指纹 + 归一化 URL。"""

    hashes: frozenset[str] = frozenset()
    urls: frozenset[str] = frozenset()

    def blocks(self, ahash: str, image_url: str) -> bool:
        """判断某张图是否应当被排除。"""
        return ahash in self.hashes or normalize_url(image_url) in self.urls


def load_exclude(paths: Sequence[Path]) -> ExcludeSet:
    """读取若干份旧 manifest，汇总成排除集合。

    Args:
        paths: manifest 文件路径列表。

    Returns:
        汇总后的排除集合；文件不存在时抛 :class:`UsageError`。
    """
    hashes: set[str] = set()
    urls: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise UsageError(f"--exclude 指向的文件不存在：{path}")
        for cell in read_cells(path):
            if cell.ahash:
                hashes.add(cell.ahash)
            urls.add(normalize_url(cell.image.image_url))
            urls.add(normalize_url(cell.image.thumb))
    return ExcludeSet(hashes=frozenset(hashes), urls=frozenset(urls))


def write_manifest(
    path: Path,
    *,
    provider: str,
    query: str,
    label: str | None,
    spec: GridSpec,
    grid_path: Path,
    cells: Sequence[Cell],
    warnings: Sequence[str],
) -> None:
    """写出 manifest。

    Args:
        path: 输出路径。
        provider: 图源名。
        query: 查询串。
        label: 调用方给的标签（可为空）。
        spec: 拼图版式。
        grid_path: 拼图文件路径。
        cells: 格子（含溯源信息）。
        warnings: 本次调用收集到的警告。
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "query": query,
        "label": label,
        "grid": {
            "path": str(grid_path),
            "cols": spec.cols,
            "rows": spec.rows,
            "cell": spec.cell,
            "width": spec.width,
            "height": spec.height,
        },
        "cells": [
            {
                "id": cell.ref_id,
                "ordinal": cell.ordinal,
                "image_url": cell.image.image_url,
                "thumb_url": cell.image.thumb,
                "page_url": cell.image.page_url,
                "title": cell.image.title,
                "width": cell.image.width,
                "height": cell.image.height,
                "license": cell.image.license,
                "ahash": cell.ahash,
                "thumb_file": cell.thumb_file,
                "local": cell.local,
            }
            for cell in cells
        ],
        "warnings": list(warnings),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_cells(path: Path, *, require_ordinal: bool = True) -> list[Cell]:
    """从 manifest 里读回格子列表。

    Args:
        path: manifest 路径。
        require_ordinal: ``True`` 时要求文件带着序号（用于 ``--pick`` 与 ``montage``）。

    Returns:
        按序号排好序的格子。

    Raises:
        ImgrefError: 文件不是合法 manifest。
    """
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImgrefError(f"无法读取 manifest {path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise ImgrefError(f"manifest 不是 JSON 对象：{path}")
    rows = payload.get("cells")
    if not isinstance(rows, list):
        raise ImgrefError(f"manifest 缺少 cells 数组：{path}")
    cells: list[Cell] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        image_url = row.get("image_url")
        provider = payload.get("provider")
        if not isinstance(image_url, str) or not isinstance(provider, str):
            continue
        ordinal = row.get("ordinal")
        if require_ordinal and not isinstance(ordinal, int):
            continue
        cells.append(
            Cell(
                ordinal=ordinal if isinstance(ordinal, int) else len(cells) + 1,
                image=ImageResult(
                    provider=provider,
                    image_url=image_url,
                    thumb_url=row.get("thumb_url") if isinstance(row.get("thumb_url"), str) else None,
                    page_url=row.get("page_url") if isinstance(row.get("page_url"), str) else None,
                    title=row.get("title") if isinstance(row.get("title"), str) else None,
                    width=row.get("width") if isinstance(row.get("width"), int) else None,
                    height=row.get("height") if isinstance(row.get("height"), int) else None,
                    license=row.get("license") if isinstance(row.get("license"), str) else None,
                ),
                ahash=row.get("ahash") if isinstance(row.get("ahash"), str) else None,
                thumb_file=row.get("thumb_file") if isinstance(row.get("thumb_file"), str) else None,
                local=row.get("local") if isinstance(row.get("local"), str) else None,
            )
        )
    return sorted(cells, key=lambda cell: cell.ordinal)


def pick_ids(ordinals: Iterable[int], manifest: Path) -> list[str]:
    """把 ``--pick 1,3,7`` 的序号翻译成 ID（必须配合 ``--from``）。

    Raises:
        UsageError: 序号在 manifest 里不存在。
    """
    by_ordinal = {cell.ordinal: cell for cell in read_cells(manifest)}
    wanted = list(ordinals)
    missing = [n for n in wanted if n not in by_ordinal]
    if missing:
        available = ", ".join(str(n) for n in sorted(by_ordinal)) or "（空）"
        raise UsageError(f"manifest 里没有这些序号：{missing}；可用序号：{available}")
    return [by_ordinal[n].ref_id for n in wanted]


def ordinal_map(manifest: Path) -> dict[str, int]:
    """返回 ``ref_id → 拼图编号``（编号是相对这一份 manifest 的）。

    取图时用它给文件命名，这样"图上的 3 号"落盘就是 ``03-…``，
    而不是"第 3 个被处理的"。
    """
    return {cell.ref_id: cell.ordinal for cell in read_cells(manifest)}
