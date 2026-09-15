"""``search`` 的编排：搜索 → 下载缩略图 → 去重 → 拼图 → 落盘。

所有"会影响结果"的判断都在这里，而且都是确定性的：
调用方拿到的是两个绝对路径 + 一张候选表，候选表里的 ID 自包含。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from imgref.ctx import Ctx
from imgref.errors import ImgrefError, NoResultsError
from imgref.fsutil import link_or_copy, prune_dirs, safe_slug
from imgref.grid import GridCell, GridSpec, ascii_title, render_grid
from imgref.imaging import ahash, image_size, sniff_ext
from imgref.manifest import Cell, load_exclude, write_manifest
from imgref.providers.base import Provider, collect, headers_for
from imgref.types import ImageResult
from imgref.urlutil import domain_of

__all__ = ["Row", "SearchOutcome", "SearchRequest", "run_search"]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """一次 ``search`` 的全部输入。"""

    provider: Provider
    query: str
    opts: object
    limit: int
    spec: GridSpec
    out_root: Path
    keep: int
    exclude: tuple[Path, ...] = ()
    label: str | None = None
    write_manifest: bool = True


@dataclass(frozen=True, slots=True)
class Row:
    """候选表里的一行。"""

    ordinal: int
    ref_id: str
    size: str
    source: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """一次 ``search`` 的全部产出。"""

    run_dir: Path
    grid_path: Path
    manifest_path: Path | None
    rows: tuple[Row, ...]
    warnings: tuple[str, ...]
    dropped: int
    candidates: int
    elapsed: float


async def run_search(ctx: Ctx, req: SearchRequest) -> SearchOutcome:
    """跑完一次搜索并渲染拼图。

    Args:
        ctx: 网络上下文（label 应为图源名）。
        req: 请求参数。

    Returns:
        产出路径、候选表与警告。

    Raises:
        NoResultsError: 图源没返回结果，或所有候选都被排除/下载失败。
    """
    started = time.monotonic()
    warnings: list[str] = []
    results = await collect(req.provider, ctx, req.query, limit=req.limit, opts=req.opts)
    if not results:
        raise NoResultsError(f"{req.provider.name} 没有返回结果：{req.query!r}")
    excluded = load_exclude(req.exclude)

    async def fetch_thumb(result: ImageResult) -> tuple[ImageResult, bytes | None, str | None]:
        url = result.thumb
        try:
            data = await ctx.get_image(url, headers=headers_for(req.provider, url))
        except ImgrefError as exc:
            return result, None, str(exc)
        return result, data, None

    fetched = await asyncio.gather(*(fetch_thumb(result) for result in results))

    kept: list[tuple[Cell, bytes]] = []
    dropped = 0
    for result, data, error in fetched:
        if data is None:
            warnings.append(f"缩略图下载失败（{result.image_url}）：{error}")
            continue
        try:
            digest = ahash(data)
        except ImgrefError as exc:
            warnings.append(f"跳过无法解码的图（{result.image_url}）：{exc}")
            continue
        if excluded.blocks(digest, result.image_url):
            dropped += 1
            continue
        kept.append((Cell(ordinal=len(kept) + 1, image=result, ahash=digest), data))

    if not kept:
        raise NoResultsError(f"候选全部被排除或下载失败（排除 {dropped} 张）")

    capacity = req.spec.capacity
    if len(kept) > capacity:
        warnings.append(f"候选 {len(kept)} 张超过拼图容量 {capacity}，只画前 {capacity} 张")
        kept = kept[:capacity]

    run_dir = _make_run_dir(req.out_root, req.provider.name, req.query)
    thumbs_dir = run_dir / "thumbs"
    grid_path = run_dir / "grid.jpg"
    cells: list[Cell] = []
    grid_cells: list[GridCell] = []
    size_labels: dict[str, str] = {}
    for cell, data in kept:
        ext = sniff_ext(data, cell.image.thumb)
        dest = thumbs_dir / f"{cell.ordinal:02d}{ext}"
        _store(ctx, cell.image.thumb, data, dest)
        stored = replace(cell, thumb_file=(Path("thumbs") / dest.name).as_posix())
        cells.append(stored)
        grid_cells.append(GridCell(label=str(stored.ordinal), data=data, note=domain_of(stored.image.page_url)[:22]))
        size_labels[stored.ref_id] = _size_label(stored.image, data)

    render_grid(grid_path, grid_cells, req.spec, ascii_title(req.provider.name, req.label, req.query))

    manifest_path: Path | None = None
    if req.write_manifest:
        manifest_path = run_dir / "results.json"
        write_manifest(
            manifest_path,
            provider=req.provider.name,
            query=req.query,
            label=req.label,
            spec=req.spec,
            grid_path=grid_path,
            cells=cells,
            warnings=warnings,
        )
    prune_dirs(req.out_root, req.keep)

    rows = tuple(
        Row(
            ordinal=cell.ordinal,
            ref_id=cell.ref_id,
            size=size_labels.get(cell.ref_id, "?"),
            source=domain_of(cell.image.page_url) or cell.image.provider,
            title=cell.image.title or "",
        )
        for cell in cells
    )
    return SearchOutcome(
        run_dir=run_dir,
        grid_path=grid_path,
        manifest_path=manifest_path,
        rows=rows,
        warnings=tuple(warnings),
        dropped=dropped,
        candidates=len(results),
        elapsed=time.monotonic() - started,
    )


def _size_label(image: ImageResult, data: bytes) -> str:
    """候选表里的尺寸列。

    图源声明了原图尺寸就用它；没声明（例如必应，它的 ``m`` blob 里根本没有宽高）
    就退回**缩略图的实测尺寸**并加 ``~`` 前缀，免得让人以为那是原图尺寸。
    """
    if image.width and image.height:
        return image.size_label
    measured = image_size(data)
    if measured is None:
        return "?"
    return f"~{measured[0]}x{measured[1]}"


def _store(ctx: Ctx, url: str, data: bytes, dest: Path) -> None:
    """把缩略图放进本次运行目录；缓存里已有同 URL 的文件就硬链接过去。"""
    cache = ctx.cache
    if cache is not None:
        source = cache.path_for(url)
        if source.is_file():
            link_or_copy(source, dest)
            return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _make_run_dir(out_root: Path, provider: str, query: str) -> Path:
    """生成 ``<out_root>/<UTC 时间戳>-<provider>-<slug>`` 形式的运行目录。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = safe_slug(query, limit=24) or "query"
    run_dir = out_root / f"{stamp}-{provider}-{slug}"
    suffix = 2
    while run_dir.exists():
        run_dir = out_root / f"{stamp}-{provider}-{slug}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
