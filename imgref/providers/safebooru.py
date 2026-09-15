"""safebooru.org 适配器：Gelbooru 0.2 风格 API，站点本身只收 SFW 内容。

实测（2026-09）：

* ``GET https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1&tags=&limit=&pid=``
* 字段：``file_url``（原图）、``sample_url``（大样，实测 850×1202，小图时 ``sample`` 为 false）、
  ``preview_url``、``width``/``height``（**原图**尺寸）、``sample_width``/``sample_height``、
  ``rating``、``source``、``directory``、``image``、``hash``、``owner``、``id``
* 分页用 ``pid``（从 0 起）
* 缩略图与原图都**不需要 Referer**（实测 206）

字段名是 ``file_url``（下划线），不是 Gelbooru 文档里常见的 ``fileurl``——按实测走。
查询串是 **booru 标签**（空格分隔），不是自然语言。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.ctx import Ctx
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.providers.base import as_int

__all__ = ["Options", "Safebooru", "parse_safebooru_json"]

_API: Final = "https://safebooru.org/index.php"
_VIEW: Final = "https://safebooru.org/index.php?page=post&s=view&id="
_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class Options:
    """safebooru 目前没有私有参数。"""


def _thumb_url(item: Mapping[str, Any]) -> str | None:
    """小图没有大样（``sample`` 为 false），那就退回原图。"""
    if item.get("sample") and isinstance(item.get("sample_url"), str) and item["sample_url"]:
        return str(item["sample_url"])
    for key in ("file_url", "preview_url"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_safebooru_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 ``page=dapi&s=post&q=index&json=1`` 的响应。

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
                provider="safebooru",
                image_url=file_url,
                thumb_url=thumb,
                page_url=f"{_VIEW}{post_id}" if post_id is not None else None,
                width=as_int(item.get("width")),
                height=as_int(item.get("height")),
                source=source if isinstance(source, str) and source else None,
            )
        )
    return out


class Safebooru:
    """safebooru.org 图库（SFW）。零配置、无需 key。"""

    name: str = "safebooru"
    label: str = "safebooru.org 图库（Gelbooru API，SFW；查询串用空格分隔的标签）"
    args: Sequence[Arg] = ()
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """该图源没有私有参数，返回空选项。"""
        return Options()

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页；``pid`` 从 0 起。"""
        page = int(cursor) if cursor else 0
        count = min(max(limit, 1), _PAGE_SIZE)
        payload = await ctx.get_json(
            _API,
            params={
                "page": "dapi",
                "s": "post",
                "q": "index",
                "json": "1",
                "tags": query.strip(),
                "limit": count,
                "pid": page,
            },
            headers={"Accept": "application/json"},
        )
        results = parse_safebooru_json(payload, count)
        return Page(results, str(page + 1) if len(results) >= count else None)
