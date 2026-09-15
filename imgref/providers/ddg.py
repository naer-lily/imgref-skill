"""DuckDuckGo 图片适配器（i.js，需要先从搜索页取 vqd token）。"""

from __future__ import annotations

import json
import re
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.errors import ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.ctx import BROWSER_UA, Ctx

__all__ = ["DuckDuckGo", "extract_vqd", "parse_ddg_json"]

_HOME: Final = "https://duckduckgo.com/"
_IJS: Final = "https://duckduckgo.com/i.js"
_VQD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"""vqd=["']([^"']+)["']"""),
    re.compile(r"vqd=([0-9][0-9-]*)"),
    re.compile(r"""name=["']vqd["'][^>]*?value=["']([^"']+)["']"""),
)
_PAGE_SIZE: Final = 100
_BROWSER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class Options:
    """ddg 的私有参数。"""

    region: str = "wt-wt"
    safe: bool = False


def extract_vqd(page: str) -> str | None:
    """从搜索页 HTML 里抠出 vqd token（脚本变量优先，其次隐藏 input / URL 形态）。"""
    for pattern in _VQD_PATTERNS:
        match = pattern.search(page)
        if match:
            return match.group(1)
    return None


def parse_ddg_json(payload: Any, limit: int) -> list[ImageResult]:
    """把 i.js 的 JSON 载荷解析成候选。

    Args:
        payload: 已解析的 JSON（结构未知，做防御式校验）。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``image`` 或 ``thumbnail`` 的条目被跳过。
    """
    if not isinstance(payload, dict):
        raise ProviderError("ddg", "parse", "i.js 响应不是 JSON 对象")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ProviderError("ddg", "parse", "i.js 响应缺少 results 数组")
    out: list[ImageResult] = []
    for row in rows:
        if len(out) >= limit:
            break
        if not isinstance(row, dict):
            continue
        image_url = row.get("image")
        thumb_url = row.get("thumbnail")
        if not isinstance(image_url, str) or not image_url or not isinstance(thumb_url, str) or not thumb_url:
            continue
        out.append(
            ImageResult(
                provider="ddg",
                image_url=image_url,
                thumb_url=thumb_url,
                page_url=row.get("url") if isinstance(row.get("url"), str) else None,
                title=row.get("title") if isinstance(row.get("title"), str) else None,
                width=row.get("width") if isinstance(row.get("width"), int) else None,
                height=row.get("height") if isinstance(row.get("height"), int) else None,
            )
        )
    return out


class DuckDuckGo:
    """DuckDuckGo 图片搜索。零配置、无需 key。"""

    name: str = "ddg"
    label: str = "DuckDuckGo 图片（i.js，无需 key）"
    args: Sequence[Arg] = (
        Arg("--region", default="wt-wt", metavar="REGION", help="地区代码（默认 wt-wt，例如 cn-zh）"),
        Arg("--safe", action="store_true", help="打开安全搜索（默认关闭，vqd 由搜索页动态给出）"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`。"""
        return Options(region=str(getattr(ns, "region", "wt-wt")), safe=bool(getattr(ns, "safe", False)))

    def headers_for(self, url: str) -> Mapping[str, str]:
        """ddg 的图来自第三方站点，带 ddg 的 referer 更容易过防盗链。"""
        return {"Referer": _HOME, "User-Agent": BROWSER_UA}

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """先取 vqd，再查 i.js；游标是对方给的 ``next`` 绝对 URL，直接照跟。"""
        count = min(max(limit, 1), _PAGE_SIZE)
        headers = {**_BROWSER_HEADERS, "Accept": "application/json", "Referer": _HOME}
        if cursor and cursor.startswith("http"):
            payload = await ctx.get_json(cursor, headers=headers)
        else:
            vqd = await self._fetch_vqd(ctx, query)
            params: dict[str, Any] = {
                "l": opts.region,
                "o": "json",
                "q": query,
                "vqd": vqd,
                "p": "1" if opts.safe else "-1",
                "s": int(cursor) if cursor else 0,
            }
            payload = await ctx.get_json(_IJS, params=params, headers=headers)
        results = parse_ddg_json(payload, count)
        next_url = payload.get("next") if isinstance(payload, dict) else None
        return Page(results, next_url if isinstance(next_url, str) and next_url.startswith("http") else None)

    async def _fetch_vqd(self, ctx: Ctx, query: str) -> str:
        """取一次 vqd token，失败时抛 :class:`ProviderError`。"""
        page = await ctx.get_text(
            _HOME,
            params={"q": query, "iax": "images", "ia": "images"},
            headers={**_BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        vqd = extract_vqd(page)
        if not vqd:
            raise ProviderError("ddg", "parse", "无法从搜索页提取 vqd token（对方页面结构可能已变）")
        return vqd
