"""``preview`` / ``download``：按自包含 ID 取图。

两个动词共用一份实现，差别只有两点：落到哪里、要不要降采样。
ID 自包含意味着这里**不需要 manifest**——只要有 ID 就能取图。
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from imgref.ctx import Ctx
from imgref.errors import IdError, ImgrefError
from imgref.fsutil import safe_slug, unique_path
from imgref.imaging import sniff_ext
from imgref.providers.base import Provider, headers_for
from imgref.types import parse_ref_id
from imgref.urlutil import domain_of

__all__ = ["GrabRequest", "Grabbed", "collect_ids", "grab"]


@dataclass(frozen=True, slots=True)
class GrabRequest:
    """一次取图请求。"""

    ids: tuple[str, ...]
    out_dir: Path
    max_edge: int = 0
    """大于 0 时把长边收敛到该值（``preview`` 用）；0 表示保留原始字节。"""
    ordinals: Mapping[str, int] = field(default_factory=dict)
    """``ref_id → 拼图编号``，用来让落盘文件名跟图上的数字对得上。

    只有调用方给了 ``--from <manifest>`` 时才知道编号；不知道就不加数字前缀，
    而不是拿"处理次序"冒充编号。
    """


@dataclass(frozen=True, slots=True)
class Grabbed:
    """一张取到的图。"""

    ref_id: str
    path: Path
    bytes_len: int
    downscaled: bool
    source_size: str
    ordinal: int | None = None
    """这张图在拼图上的编号；调用方没提供 manifest 时为 ``None``。"""


async def grab(ctx: Ctx, req: GrabRequest, providers: Mapping[str, Provider]) -> tuple[list[Grabbed], list[str]]:
    """并发取若干张图。

    Args:
        ctx: 网络上下文。
        req: 请求参数。
        providers: 图源注册表（用来取下载时需要的请求头）。

    Returns:
        ``(成功的文件列表, 警告列表)``；单张失败不会影响其它张。

    Raises:
        IdError: 某个 ID 格式非法，或其图源不存在。
    """
    plan: list[tuple[str, str, str]] = []
    for raw in req.ids:
        provider_name, url = parse_ref_id(raw)
        provider = providers.get(provider_name)
        if provider is None:
            known = "、".join(sorted(providers))
            raise IdError(f"ID 里的图源 {provider_name!r} 不存在；已知：{known}")
        plan.append((raw, provider_name, url))

    req.out_dir.mkdir(parents=True, exist_ok=True)

    async def one(index: int, item: tuple[str, str, str]) -> Grabbed:
        raw, provider_name, url = item
        provider = providers[provider_name]
        data = await ctx.get_image(url, headers=headers_for(provider, url))
        ext = sniff_ext(data, url)
        stem = safe_slug(Path(url.split("?")[0]).stem or domain_of(url), limit=40)
        ordinal = req.ordinals.get(raw)
        prefix = f"{ordinal:02d}-" if ordinal is not None else ""
        dest = unique_path(req.out_dir / f"{prefix}{stem}{ext}")
        source_size = _size_label(data)
        downscaled = False
        if req.max_edge > 0:
            data, downscaled = _downscale(data, req.max_edge)
            if downscaled:
                dest = dest.with_suffix(".jpg")
        dest.write_bytes(data)
        return Grabbed(
            ref_id=raw,
            path=dest,
            bytes_len=len(data),
            downscaled=downscaled,
            source_size=source_size,
            ordinal=ordinal,
        )

    gathered = await asyncio.gather(
        *(one(index, item) for index, item in enumerate(plan, start=1)),
        return_exceptions=True,
    )
    files: list[Grabbed] = []
    warnings: list[str] = []
    for item, outcome in zip(plan, gathered, strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, IdError):
                raise outcome
            warnings.append(f"取图失败（{item[0]}）：{outcome}")
            continue
        files.append(outcome)
    return files, warnings


def collect_ids(
    positional: Sequence[str],
    *,
    ids_file: Path | None = None,
    pick: Sequence[str] = (),
) -> list[str]:
    """汇总本次要取的 ID：位置参数 + ``--ids-file`` + ``--pick``。

    Args:
        positional: 命令行上直接给的 ID。
        ids_file: 每行一个 ID 的文件。
        pick: 已经由 ``--pick`` + ``--from`` 翻译好的 ID。

    Returns:
        去重后保持顺序的 ID 列表。

    Raises:
        ImgrefError: ``--ids-file`` 读不出来。
    """
    collected: list[str] = list(pick)
    collected.extend(positional)
    if ids_file is not None:
        try:
            text = ids_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ImgrefError(f"无法读取 --ids-file {ids_file}：{exc}") from exc
        collected.extend(line.strip() for line in text.splitlines() if line.strip())
    seen: set[str] = set()
    out: list[str] = []
    for raw in collected:
        item = raw.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _size_label(data: bytes) -> str:
    """图片的``宽x高``标签，解不出来时给 ``?``。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return f"{image.width}x{image.height}"
    except (OSError, ValueError):
        return "?"


def _downscale(data: bytes, max_edge: int) -> tuple[bytes, bool]:
    """把长边收敛到 ``max_edge``；本来就够小就原样返回。"""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
            if max(image.size) <= max_edge:
                return data, False
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90, optimize=True, subsampling=0)
            return buffer.getvalue(), True
    except (OSError, ValueError):
        return data, False
