"""堆糖适配器：中文图片站的非官方 napi，无需 key。

实测（2026-09）：

* ``GET https://www.duitang.com/napi/blog/list/by_search/?kw=<词>&start=<偏移>&limit=<n>``
* 响应 ``{"status":1,"data":{"total":720,"next_start":2,"object_list":[…]}}``
* 条目：``id``、``msg``（标题）、``photo`` → ``{"id","width","height","path","size",…}``
* ``photo.path`` 就是图本身（没有单独的缩略图）；**宽高是字符串**（``"804"``），要转
* 分页用 ``next_start``（**偏移，不是页号**）——正好是本项目"不透明游标"的用法
* ``https://www.duitang.com/blog/?id=<条目 id>`` 实测 200，可作为来源页
* 诚实 UA 与 Referer 都不需要（实测都返回 200；图片 CDN 也直接 206）

注意：图片 CDN 是 ``a-ssl.dtstatic.com``，URL 不带签名，所以可以存下来稍后再下。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.errors import ProviderError
from imgref.ctx import Ctx
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.providers.base import as_int

__all__ = ["Duitang", "Options", "next_start", "parse_duitang_json"]

_API: Final = "https://www.duitang.com/napi/blog/list/by_search/"
_BLOG: Final = "https://www.duitang.com/blog/?id="
_PAGE_SIZE: Final = 50


@dataclass(frozen=True, slots=True)
class Options:
    """堆糖目前没有私有参数。"""


def parse_duitang_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 ``by_search`` 的响应。

    Args:
        payload: 已解析的 JSON。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``photo.path`` 的条目被跳过。

    Raises:
        ProviderError: ``status`` 不是 1，或结构不是预期形态。
    """
    if not isinstance(payload, dict):
        raise ProviderError("duitang", "parse", "响应不是 JSON 对象")
    if as_int(payload.get("status")) != 1:
        raise ProviderError("duitang", "parse", f"status 不是 1：{payload.get('status')!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderError("duitang", "parse", "响应缺少 data 对象")
    items = data.get("object_list")
    if not isinstance(items, list):
        return []
    out: list[ImageResult] = []
    for item in items:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        photo = item.get("photo")
        if not isinstance(photo, Mapping):
            continue
        path = photo.get("path")
        if not isinstance(path, str) or not path:
            continue
        post_id = as_int(item.get("id"))
        title = item.get("msg")
        out.append(
            ImageResult(
                provider="duitang",
                image_url=path,
                thumb_url=path,
                page_url=f"{_BLOG}{post_id}" if post_id is not None else None,
                title=title if isinstance(title, str) and title else None,
                width=as_int(photo.get("width")),
                height=as_int(photo.get("height")),
            )
        )
    return out


def next_start(payload: Any, current: int) -> Cursor | None:
    """从响应里取出下一次的 ``next_start``（偏移游标）。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    value = as_int(data.get("next_start"))
    if value is None or value <= current:
        return None
    return str(value)


class Duitang:
    """堆糖。零配置、无需 key（非官方接口，对方改版就可能失效）。"""

    name: str = "duitang"
    label: str = "堆糖（中文图片站，非官方 napi，无需 key）"
    args: Sequence[Arg] = ()
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """该图源没有私有参数，返回空选项。"""
        return Options()

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页；游标是 ``next_start``（偏移）。"""
        start = int(cursor) if cursor else 0
        count = min(max(limit, 1), _PAGE_SIZE)
        payload = await ctx.get_json(
            _API,
            params={"kw": query.strip(), "start": start, "limit": count},
            headers={"Accept": "application/json"},
        )
        results = parse_duitang_json(payload, count)
        return Page(results, next_start(payload, start))
