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

import xml.etree.ElementTree as ElementTree
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.ctx import Ctx
from imgref.errors import ImgrefError, ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.providers.base import as_int, format_tag_problems, is_plain_tag

__all__ = ["Options", "Safebooru", "parse_safebooru_json", "parse_tag_xml"]

_API: Final = "https://safebooru.org/index.php"
_VIEW: Final = "https://safebooru.org/index.php?page=post&s=view&id="
_PAGE_SIZE: Final = 100
_NEAR_LIMIT: Final = 4


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


def parse_tag_xml(text: str) -> list[tuple[str, int]]:
    """解析 Gelbooru tag 接口的 XML，返回 ``[(标签名, 帖子数)]``。

    这个端点**忽略 `json=1`**，只给 XML；用标准库 ``xml.etree`` 解，不值得为此加依赖。

    Args:
        text: 响应文本，形如 ``<tags><tag name="landscape" count="9774"/></tags>``。

    Returns:
        标签名与帖子数；**空列表表示"这个标签不存在"**（对方对不存在的标签就回空 ``<tags>``）。

    Raises:
        ProviderError: 响应根本不是 XML——那说明"读不到答案"，不能当成"标签不存在"，
            否则对方一改接口就会把所有合法查询都判成错的。
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ProviderError("safebooru", "parse", f"标签接口没有返回 XML：{exc}") from exc
    out: list[tuple[str, int]] = []
    for element in root.iter("tag"):
        name = element.get("name")
        if name:
            out.append((name, as_int(element.get("count")) or 0))
    return out


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

    async def _lookup_tag(self, ctx: Ctx, tag: str) -> tuple[bool, list[str]]:
        """查一个标签是否存在，返回 ``(是否存在, 相近候选)``。

        这个端点**只给 XML**（实测加 ``json=1`` 也还是 XML），用标准库解，不引依赖。
        """
        text = await ctx.get_text(
            _API,
            params={"page": "dapi", "s": "tag", "q": "index", "json": "1", "name": tag},
            headers={"Accept": "application/json"},
        )
        names = [name for name, _ in parse_tag_xml(text)]
        if tag in names:
            return True, []
        pattern = await ctx.get_text(
            _API,
            params={"page": "dapi", "s": "tag", "q": "index", "json": "1", "name_pattern": f"{tag}%", "limit": 5},
            headers={"Accept": "application/json"},
        )
        near = [name for name, _ in parse_tag_xml(pattern) if name != tag][: _NEAR_LIMIT]
        return False, near

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """先校验标签，再查一页（``pid`` 从 0 起）。"""
        page = int(cursor) if cursor else 0
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
                    raise ProviderError("safebooru", "query", format_tag_problems("safebooru.org", missing, suggestions))
                if missing:
                    kept = [tag for tag in tags if tag not in missing]
                    notes.append(
                        f"{format_tag_problems('safebooru.org', missing, suggestions)}；已改用：{' '.join(kept)}"
                    )
                    tags = kept
        payload = await ctx.get_json(
            _API,
            params={
                "page": "dapi",
                "s": "post",
                "q": "index",
                "json": "1",
                "tags": " ".join(tags).strip(),
                "limit": count,
                "pid": page,
            },
            headers={"Accept": "application/json"},
        )
        results = parse_safebooru_json(payload, count)
        return Page(results, str(page + 1) if len(results) >= count else None, tuple(notes))
