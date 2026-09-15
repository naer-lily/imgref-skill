"""维基共享资源（Commons）适配器：免费、无需 key、带版权信息。"""

from __future__ import annotations

import json
import re
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.errors import ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.ctx import Ctx

__all__ = ["Wikimedia", "parse_wikimedia_json"]

_API: Final = "https://commons.wikimedia.org/w/api.php"
_TAGS: Final = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class Options:
    """wikimedia 的私有参数。"""

    mime: str = "any"
    thumb_width: int = 400


def _strip_html(text: str) -> str:
    """去掉许可字段里的 HTML 标签与实体。"""
    return _TAGS.sub("", text).replace("&amp;", "&").replace("&quot;", '"').strip()


def parse_wikimedia_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 Commons ``generator=search`` 的响应。

    Args:
        payload: 已解析的 JSON。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``imageinfo`` 或缺 ``url``/``thumburl`` 的页被跳过。
    """
    if not isinstance(payload, dict):
        raise ProviderError("wikimedia", "parse", "API 响应不是 JSON 对象")
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, dict):
        return []
    out: list[ImageResult] = []
    for page in pages.values():
        if len(out) >= limit:
            break
        if not isinstance(page, dict):
            continue
        infos = page.get("imageinfo")
        if not isinstance(infos, list) or not infos:
            continue
        info = infos[0]
        if not isinstance(info, dict):
            continue
        image_url = info.get("url")
        thumb_url = info.get("thumburl")
        if not isinstance(image_url, str) or not image_url or not isinstance(thumb_url, str) or not thumb_url:
            continue
        license_name = ""
        extmeta = info.get("extmetadata")
        if isinstance(extmeta, dict):
            short = extmeta.get("LicenseShortName")
            if isinstance(short, dict) and isinstance(short.get("value"), str):
                license_name = _strip_html(short["value"])
        title = page.get("title")
        out.append(
            ImageResult(
                provider="wikimedia",
                image_url=image_url,
                thumb_url=thumb_url,
                page_url=info.get("descriptionurl") if isinstance(info.get("descriptionurl"), str) else None,
                title=title if isinstance(title, str) else None,
                width=info.get("width") if isinstance(info.get("width"), int) else None,
                height=info.get("height") if isinstance(info.get("height"), int) else None,
                license=license_name or None,
            )
        )
    return out


class Wikimedia:
    """维基共享资源。零配置、无需 key。"""

    name: str = "wikimedia"
    label: str = "维基共享资源 API（无需 key，带版权信息）"
    args: Sequence[Arg] = (
        Arg("--mime", choices=("any", "jpeg", "png"), default="any", help="只搜指定格式（默认 any）"),
        Arg("--thumb-width", type=int, default=400, metavar="PX", help="请求的缩略图宽度（默认 400）"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`。"""
        return Options(mime=str(getattr(ns, "mime", "any")), thumb_width=int(getattr(ns, "thumb_width", 400)))

    def headers_for(self, url: str) -> Mapping[str, str]:
        """upload.wikimedia.org 允许直连，不需要额外头。"""
        return {}

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页；游标是 MediaWiki 的 ``continue`` 对象序列化结果（对它不透明）。"""
        count = min(max(limit, 1), 50)
        search = f"filemime:image/{opts.mime} {query}" if opts.mime != "any" else query
        params: dict[str, Any] = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search,
            "gsrnamespace": 6,
            "gsrlimit": count,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": opts.thumb_width,
            "origin": "*",
        }
        if cursor:
            continuation = json.loads(cursor)
            if isinstance(continuation, dict):
                params.update({str(k): str(v) for k, v in continuation.items()})
        payload = await ctx.get_json(_API, params=params, headers={"Accept": "application/json"})
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("info"):
                raise ProviderError("wikimedia", "http", f"API 报错：{error['info']}")
        results = parse_wikimedia_json(payload, count)
        continuation = payload.get("continue") if isinstance(payload, dict) else None
        next_cursor = json.dumps(continuation, sort_keys=True) if isinstance(continuation, dict) else None
        return Page(results, next_cursor)
