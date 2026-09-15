"""yande.re 适配器：Danbooru 系插画图库，官方 JSON API，无需 key。

实测（2026-09）：

* ``GET https://yande.re/post.json?tags=<空格分隔的标签>&limit=<n>&page=<n>``
* 字段：``file_url``（原图）、``sample_url``（大样，实测 1061×1500）、``preview_url``、
  ``width``/``height``（**原图**尺寸）、``sample_width``/``sample_height``、
  ``rating``（``s``/``q``/``e``）、``source``（原作者页，实测是 pixiv 作品页）、
  ``md5``、``tags``、``id``
* 分页用 ``page``（从 1 起）；``rating:s`` 作为标签过滤**实测有效**
* 缩略图与原图都**不需要 Referer**（实测 206）

注意：这里的查询串是 **booru 标签**（空格分隔），不是自然语言。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.ctx import Ctx
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.providers.base import as_int

__all__ = ["Options", "Yandere", "parse_yandere_json"]

_API: Final = "https://yande.re/post.json"
_POST: Final = "https://yande.re/post/show/"
_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class Options:
    """yandere 的私有参数。"""

    rating: str = "safe"


def _thumb_url(item: Mapping[str, Any]) -> str | None:
    """拼图用大样而不是小预览：``preview_url`` 只有 150px 级别，判不了图。"""
    for key in ("sample_url", "file_url", "preview_url"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_yandere_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 ``post.json`` 的响应。

    Args:
        payload: 已解析的 JSON（应为条目数组）。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``file_url`` 或缩略图的条目被跳过。
    """
    if not isinstance(payload, list):
        return []
    out: list[ImageResult] = []
    for item in payload:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        file_url = item.get("file_url")
        thumb = _thumb_url(item)
        if not isinstance(file_url, str) or not file_url or thumb is None:
            continue
        post_id = as_int(item.get("id"))
        source = item.get("source")
        out.append(
            ImageResult(
                provider="yandere",
                image_url=file_url,
                thumb_url=thumb,
                page_url=f"{_POST}{post_id}" if post_id is not None else None,
                width=as_int(item.get("width")),
                height=as_int(item.get("height")),
                source=source if isinstance(source, str) and source else None,
            )
        )
    return out


class Yandere:
    """yande.re 插画图库。零配置、无需 key。"""

    name: str = "yandere"
    label: str = "yande.re 插画图库（官方 API；查询串用空格分隔的标签）"
    args: Sequence[Arg] = (
        Arg("--rating", choices=("safe", "all"), default="safe", help="内容分级（默认 safe；all 不加上限过滤）"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`。"""
        return Options(rating=str(getattr(ns, "rating", "safe")))

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页；``page`` 从 1 起。"""
        page = int(cursor) if cursor else 1
        count = min(max(limit, 1), _PAGE_SIZE)
        tags = query.strip() if opts.rating == "all" else f"{query.strip()} rating:s".strip()
        payload = await ctx.get_json(
            _API,
            params={"tags": tags, "limit": count, "page": page},
            headers={"Accept": "application/json"},
        )
        results = parse_yandere_json(payload, count)
        return Page(results, str(page + 1) if len(results) >= count else None)
