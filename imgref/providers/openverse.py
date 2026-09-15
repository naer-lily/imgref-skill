"""Openverse 适配器：CC 授权图库，匿名可用，也可配 token 提高配额。"""

from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from imgref.errors import ProviderError
from imgref.types import Arg, Cursor, ImageResult, Page

from imgref.ctx import Ctx

__all__ = ["Openverse", "parse_openverse_json"]

_API: Final = "https://api.openverse.org/v1/images/"
_TOKEN_ENV: Final = "OPENVERSE_TOKEN"
_PAGE_SIZE: Final = 20


@dataclass(frozen=True, slots=True)
class Options:
    """openverse 的私有参数。"""

    license_type: str = "all"
    token: str = ""


def parse_openverse_json(payload: Any, limit: int) -> list[ImageResult]:
    """解析 Openverse ``/v1/images/`` 响应。

    Args:
        payload: 已解析的 JSON。
        limit: 最多返回多少条。

    Returns:
        候选列表；缺 ``url`` 的条目被跳过。
    """
    if not isinstance(payload, dict):
        raise ProviderError("openverse", "parse", "API 响应不是 JSON 对象")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ProviderError("openverse", "parse", "API 响应缺少 results 数组")
    out: list[ImageResult] = []
    for row in rows:
        if len(out) >= limit:
            break
        if not isinstance(row, dict):
            continue
        image_url = row.get("url")
        if not isinstance(image_url, str) or not image_url:
            continue
        thumb = row.get("thumbnail")
        title = row.get("title")
        landing = row.get("foreign_landing_url")
        out.append(
            ImageResult(
                provider="openverse",
                image_url=image_url,
                thumb_url=thumb if isinstance(thumb, str) and thumb else image_url,
                page_url=landing if isinstance(landing, str) else None,
                title=title if isinstance(title, str) else None,
                width=row.get("width") if isinstance(row.get("width"), int) else None,
                height=row.get("height") if isinstance(row.get("height"), int) else None,
                license=row.get("license") if isinstance(row.get("license"), str) else None,
            )
        )
    return out


class Openverse:
    """Openverse 图片 API。零配置可用，配 token 后配额更高。"""

    name: str = "openverse"
    label: str = "Openverse（CC 授权图库，匿名可用，token 可选）"
    args: Sequence[Arg] = (
        Arg("--license-type", choices=("all", "commercial", "modification"), default="all", help="按许可类型过滤（默认 all）"),
        Arg("--token", metavar="TOKEN", help=f"Openverse API token；缺省读环境变量 {_TOKEN_ENV}"),
    )
    requires: Sequence[str] = ()

    def parse_options(self, ns: Namespace) -> Options:
        """把 argparse 结果转成 :class:`Options`（token 缺省时读环境变量）。"""
        token = str(getattr(ns, "token", "") or os.environ.get(_TOKEN_ENV, ""))
        return Options(license_type=str(getattr(ns, "license_type", "all")), token=token)

    def headers_for(self, url: str) -> Mapping[str, str]:
        """Openverse 的图挂在自己的 CDN 上，不需要额外头。"""
        return {}

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Options) -> Page:
        """查一页 Openverse 结果。"""
        page = int(cursor) if cursor else 1
        count = min(max(limit, 1), _PAGE_SIZE)
        params: dict[str, Any] = {"q": query, "page_size": count, "page": page, "mature": "false"}
        if opts.license_type != "all":
            params["license_type"] = opts.license_type
        headers = {"Accept": "application/json"}
        if opts.token:
            headers["Authorization"] = f"Bearer {opts.token}"
        payload = await ctx.get_json(_API, params=params, headers=headers)
        results = parse_openverse_json(payload, count)
        page_count = payload.get("page_count") if isinstance(payload, dict) else None
        has_next = isinstance(page_count, int) and page < page_count
        return Page(results, str(page + 1) if has_next else None)
