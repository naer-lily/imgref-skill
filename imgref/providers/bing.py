"""必应图片抓取适配器（HTML，无需 key，通常最快最稳）。"""

from __future__ import annotations

import html as html_module
import json
import re
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.ctx import BROWSER_UA, Ctx

__all__ = ["Bing", "parse_bing_html"]

_ENDPOINT: Final = "https://www.bing.com/images/async"
_META_RE: Final = re.compile(r'class="iusc"[^>]*?m="([^"]*)"')
_PAGE_SIZE: Final = 35
_BROWSER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bing.com/images/search",
}


@dataclass(frozen=True, slots=True)
class Options:
    """bing 的私有参数。"""

    safe: str = "moderate"


def parse_bing_html(page: str, limit: int) -> list[ImageResult]:
    """从必应异步图片页里解析候选（``a.iusc`` 的 ``m="…"`` JSON 块）。

    实测（2026-09）blob 的字段是 ``sid/cturl/cid/purl/murl/turl/md5/shkey/t/mid/desc``：
    **没有原图宽高**，页面 URL 字段叫 ``purl``。``mw``/``mh`` 只在旧版或部分变体里出现，
    所以仍然容忍性地读一下，但不要依赖它。

    Args:
        page: HTML 文本。
        limit: 最多返回多少条。

    Returns:
        解析成功的候选；缺 ``murl`` 或 ``turl`` 的块被跳过，坏 JSON 被跳过。
    """
    out: list[ImageResult] = []
    for raw in _META_RE.findall(page):
        if len(out) >= limit:
            break
        try:
            meta: Any = json.loads(html_module.unescape(raw))
        except ValueError:
            continue
        if not isinstance(meta, dict):
            continue
        image_url = meta.get("murl")
        thumb_url = meta.get("turl")
        if not isinstance(image_url, str) or not image_url or not isinstance(thumb_url, str) or not thumb_url:
            continue
        page_url = meta.get("purl")
        out.append(
            ImageResult(
                provider="bing",
                image_url=image_url,
                thumb_url=thumb_url,
                page_url=page_url if isinstance(page_url, str) else None,
                title=meta.get("t") if isinstance(meta.get("t"), str) else None,
                width=meta.get("mw") if isinstance(meta.get("mw"), int) else None,
                height=meta.get("mh") if isinstance(meta.get("mh"), int) else None,
            )
        )
    return out


class Bing:
    """必应图片。零配置、无需 key。"""

    name: str = "bing"
    label: str = "必应图片（HTML 抓取，无需 key，通常最快）"
    args: Sequence[Arg] = (
        Arg("--safe", choices=("off", "moderate", "strict"), default="moderate", help="成人内容过滤级别（默认 moderate）"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`。"""
        return Options(safe=str(getattr(ns, "safe", "moderate")))

    def headers_for(self, url: str) -> Mapping[str, str]:
        """必应缩略图有热链保护，必须带 referer（并用同一个浏览器 UA）。"""
        return {"Referer": "https://www.bing.com/", "User-Agent": BROWSER_UA, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页必应异步结果。"""
        first = int(cursor) if cursor else 1
        count = min(max(limit, 1), _PAGE_SIZE)
        params: dict[str, Any] = {"q": query, "first": first, "count": count, "mmasync": "1"}
        if opts.safe != "moderate":
            params["adlt"] = opts.safe
        page = await ctx.get_text(_ENDPOINT, params=params, headers=_BROWSER_HEADERS)
        results = parse_bing_html(page, count)
        return Page(results, str(first + count) if len(results) >= count else None)
