"""yande.re 适配器：Danbooru 系插画图库，官方 JSON API，无需 key。

实测（2026-09）：

* ``GET https://yande.re/post.json?tags=<空格分隔的标签>&limit=<n>&page=<n>``
* 字段：``file_url``（原图）、``sample_url``（大样，实测 1061×1500）、``preview_url``、
  ``width``/``height``（**原图**尺寸）、``sample_width``/``sample_height``、
  ``rating``（``s``/``q``/``e``）、``source``（原作者页，实测是 pixiv 作品页）、
  ``md5``、``tags``、``id``
* 分页用 ``page``（从 1 起）；``rating:s`` 作为标签过滤**实测有效**
* 缩略图与原图都**不需要 Referer**（实测 206）
* ``GET /tag.json?name=<子串>&limit=<n>`` → ``[{id,name,count,type,ambiguous}]``

注意：查询串是 **booru 标签**（空格分隔），不是自然语言；词表是站内自有的
（``landscape`` 有 6763 张，``scenery``/``no_humans`` **不存在**）。所以搜索前会逐个
标签校验一次——**这不是锦上添花：标签写错时接口只回「200 + 空数组」，跟"真没搜到"
长得一模一样**，调用方会白改关键词。

为什么是逐个精确查、而不是拉全量标签表：``/tag.json?limit=0`` 实测 **9.8MB / 8.4s**，
单标签精确查只要 **0.1KB / 0.5s**；而且 ``name`` 是子串匹配，一次请求顺带把"相近标签"
带回来了。批量也不行（``name=a,b`` 返回空）。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.ctx import Ctx
from imgref.errors import ImgrefError, ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.providers.base import as_int, format_tag_problems, is_plain_tag

__all__ = ["Options", "Yandere", "parse_yandere_json"]

_API: Final = "https://yande.re/post.json"
_TAG_API: Final = "https://yande.re/tag.json"
_POST: Final = "https://yande.re/post/show/"
_PAGE_SIZE: Final = 100
_NEAR_LIMIT: Final = 4
_JSON: Final[dict[str, str]] = {"Accept": "application/json"}


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
    label: str = "yande.re 插画图库（官方 API；空格分隔的标签，会先校验标签是否存在）"
    args: Sequence[Arg] = (
        Arg("--rating", choices=("safe", "all"), default="safe", help="内容分级（默认 safe；all 不加上限过滤）"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`。"""
        return Options(rating=str(getattr(ns, "rating", "safe")))

    async def _lookup_tag(self, ctx: Ctx, tag: str) -> tuple[bool, list[str]]:
        """查一个标签是否存在，返回 ``(是否存在, 相近候选)``。

        ``name`` 是**子串**匹配，所以不存在时返回的那些名字正好当相近候选用。
        结构不认识时按"存在"处理——宁可漏报，也不能误伤合法查询。
        """
        payload = await ctx.get_json(_TAG_API, params={"name": tag, "limit": 10}, headers=_JSON)
        if not isinstance(payload, list):
            return True, []
        names = [item["name"] for item in payload if isinstance(item, dict) and isinstance(item.get("name"), str)]
        if tag in names:
            return True, []
        return False, names[:_NEAR_LIMIT]

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """先校验标签，再查一页（``page`` 从 1 起）。"""
        page_number = int(cursor) if cursor else 1
        count = min(max(limit, 1), _PAGE_SIZE)
        tags = query.split()
        plain = [tag for tag in tags if is_plain_tag(tag)]
        notes: list[str] = []
        # 标签校验是"查询"的属性，不是"页"的属性：只在第一页做，翻页不重复查
        if plain and cursor is None:
            missing: list[str] = []
            suggestions: dict[str, list[str]] = {}
            try:
                for tag in plain:
                    exists, near = await self._lookup_tag(ctx, tag)
                    if not exists:
                        missing.append(tag)
                        suggestions[tag] = near
            except ImgrefError as exc:  # 校验本身失败不该拖垮搜索
                notes.append(f"标签校验跳过（{exc}）")
            else:
                if missing and len(missing) == len(plain):
                    raise ProviderError("yandere", "query", format_tag_problems("yande.re", missing, suggestions))
                if missing:
                    kept = [tag for tag in tags if tag not in missing]
                    notes.append(f"{format_tag_problems('yande.re', missing, suggestions)}；已改用：{' '.join(kept)}")
                    tags = kept
        if opts.rating != "all":
            tags.append("rating:s")
        payload = await ctx.get_json(
            _API,
            params={"tags": " ".join(tags).strip(), "limit": count, "page": page_number},
            headers=_JSON,
        )
        results = parse_yandere_json(payload, count)
        return Page(results, str(page_number + 1) if len(results) >= count else None, tuple(notes))
