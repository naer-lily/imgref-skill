"""``montage``：把多轮 ``search`` 的拼图合并成一张（边缘 helper）。

它**不参与主流程**：主流程是"一个图源 + 一个查询串 → 一张拼图"，
合并的编排权属于调用方。这个命令只是省掉调用方自己拼图那点麻烦，
直接复用各轮已经落盘的缩略图，不发任何网络请求。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from imgref.errors import ImgrefError
from imgref.grid import GridCell, GridSpec, ascii_title, render_grid
from imgref.manifest import Cell, read_cells
from imgref.urlutil import domain_of

__all__ = ["MontageOutcome", "run_montage"]


@dataclass(frozen=True, slots=True)
class MontageOutcome:
    """合并结果。"""

    grid_path: Path
    manifest_path: Path | None
    cells: tuple[Cell, ...]
    warnings: tuple[str, ...]


def run_montage(
    manifests: Sequence[Path],
    out_path: Path,
    *,
    title: str = "",
    spec: GridSpec | None = None,
    write_json: bool = True,
) -> MontageOutcome:
    """读若干份 manifest，重新连续编号后合并成一张拼图。

    Args:
        manifests: 各轮的 ``results.json``。
        out_path: 输出拼图路径。
        title: 顶部标题（会被剥成 ASCII）。
        spec: 版式；``None`` 用默认 4×3。
        write_json: 是否在同目录写一份 ``merged.json`` 记录新序号到原 ID 的映射。

    Returns:
        产出路径、合并后的格子与新编号映射。

    Raises:
        ImgrefError: 某个 manifest 读不出来，或一张图都没找到。
    """
    layout = spec if spec is not None else GridSpec()
    warnings: list[str] = []
    collected: list[Cell] = []
    for manifest in manifests:
        source_dir = manifest.parent
        for cell in read_cells(manifest):
            local = _resolve(source_dir, cell)
            if local is None:
                warnings.append(f"缩略图缺失，跳过：{cell.ref_id}")
                continue
            collected.append(replace(cell, local=str(local)))
    if not collected:
        raise ImgrefError("没有任何可用缩略图可以合并（检查各轮 manifest 与 thumbs 目录）")
    if len(collected) > layout.capacity:
        warnings.append(f"合并结果 {len(collected)} 格超过拼图容量 {layout.capacity}，只画前 {layout.capacity} 格")
        collected = collected[: layout.capacity]
    merged = [replace(cell, ordinal=index) for index, cell in enumerate(collected, start=1)]

    grid_cells: list[GridCell] = []
    for cell in merged:
        path = Path(cell.local or "")
        if not path.is_file():
            continue
        grid_cells.append(GridCell(label=str(cell.ordinal), data=path.read_bytes(), note=domain_of(cell.image.page_url)[:22]))
    render_grid(out_path, grid_cells, layout, ascii_title("montage", title, " + ".join(str(m.parent.name) for m in manifests)))

    manifest_path: Path | None = None
    if write_json:
        manifest_path = out_path.with_suffix(".json")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "grid": {"path": str(out_path), "cols": layout.cols, "rows": layout.rows, "cell": layout.cell},
            "sources": [str(manifest) for manifest in manifests],
            "cells": [
                {
                    "ordinal": cell.ordinal,
                    "id": cell.ref_id,
                    "provider": cell.image.provider,
                    "image_url": cell.image.image_url,
                    "page_url": cell.image.page_url,
                    "title": cell.image.title,
                    "width": cell.image.width,
                    "height": cell.image.height,
                    "license": cell.image.license,
                    "source": cell.image.source,
                    "ahash": cell.ahash,
                    "local": cell.local,
                }
                for cell in merged
            ],
            "warnings": warnings,
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return MontageOutcome(grid_path=out_path, manifest_path=manifest_path, cells=tuple(merged), warnings=tuple(warnings))


def _resolve(source_dir: Path, cell: Cell) -> Path | None:
    """找到这一格在本轮的缩略图文件。"""
    if cell.thumb_file:
        candidate = source_dir / cell.thumb_file
        if candidate.is_file():
            return candidate
    if cell.local:
        candidate = Path(cell.local)
        if candidate.is_file():
            return candidate
    return None
