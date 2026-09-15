"""Serper.dev 谷歌图片适配器：需要 ``SERPER_API_KEY``，默认在帮助里标为未配置。"""

from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.errors import ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.ctx import Ctx

__all__ = ["Serper", "parse_serper_json"]

_API: Final = "https://google.serper.dev/images"
_KEY_ENV: Final = "SERPER_API_KEY"
_PAGE_SIZE: Final = 20


@dataclass(frozen=True, slots=True)
class Options:
    """serper 的私有参数。"""

    gl: str = "cn"
    api_key: str = ""


def parse_serper_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 Serper 图片响应。

    Args:
        payload: 已解析的 JSON。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``imageUrl`` 的条目被跳过。
    """
    if not isinstance(payload, dict):
        raise ProviderError("serper", "parse", "API 响应不是 JSON 对象")
    rows = payload.get("images")
    if not isinstance(rows, list):
        raise ProviderError("serper", "parse", "API 响应缺少 images 数组")
    out: list[ImageResult] = []
    for row in rows:
        if len(out) >= limit:
            break
        if not isinstance(row, dict):
            continue
        image_url = row.get("imageUrl")
        if not isinstance(image_url, str) or not image_url:
            continue
        thumb = row.get("thumbnailUrl")
        link = row.get("link")
        title = row.get("title")
        out.append(
            ImageResult(
                provider="serper",
                image_url=image_url,
                thumb_url=thumb if isinstance(thumb, str) and thumb else image_url,
                page_url=link if isinstance(link, str) else None,
                title=title if isinstance(title, str) else None,
                width=row.get("imageWidth") if isinstance(row.get("imageWidth"), int) else None,
                height=row.get("imageHeight") if isinstance(row.get("imageHeight"), int) else None,
            )
        )
    return out


class Serper:
    """Serper.dev 谷歌图片 API（付费 key）。"""

    name: str = "serper"
    label: str = "Serper.dev 谷歌图片（付费 API）"
    args: Sequence[Arg] = (
        Arg("--gl", default="cn", metavar="CC", help="国家代码（默认 cn）"),
        Arg("--api-key", metavar="KEY", help=f"Serper API key；缺省读环境变量 {_KEY_ENV}"),
    )
    requires: Sequence[str] = (_KEY_ENV,)

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`（key 缺省时读环境变量）。"""
        return Options(gl=str(getattr(ns, "gl", "cn")), api_key=str(getattr(ns, "api_key", "") or os.environ.get(_KEY_ENV, "")))

    def headers_for(self, url: str) -> Mapping[str, str]:
        """谷歌图源在三方站点上，不需要额外头。"""
        return {}

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页 Serper 结果（POST + X-API-KEY）。"""
        if not opts.api_key:
            raise ProviderError("serper", "auth", f"缺少 API key：设置 {_KEY_ENV} 或传 --api-key")
        page = int(cursor) if cursor else 1
        count = min(max(limit, 1), _PAGE_SIZE)
        body: dict[str, Any] = {"q": query, "num": count, "page": page, "gl": opts.gl}
        payload = await ctx.post_json(
            _API,
            body=body,
            headers={"X-API-KEY": opts.api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        results = parse_serper_json(payload, count)
        return Page(results, str(page + 1) if len(results) >= count else None)
