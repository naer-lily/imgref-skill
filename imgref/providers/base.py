"""图源适配器协议与框架侧的公共工具。

**这里没有基类。** 一个图源就是一个普通类，只要满足 :class:`Provider` 的形状即可：
Protocol 会在注册表那里逐一做静态检查，所以既不用继承、也没有 ``super()``，
但类型安全一分不少。

可选能力用**独立的 Protocol**表达（``ProvidesHeaders`` / ``ParsesOptions``），
而不是塞进主协议——"下载原图时要带什么 referer"是图源自己的事，
框架完全不需要知道 referer 这个概念。
"""

from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from imgref.types import Arg, Cursor, ImageResult, Page
from imgref.urlutil import normalize_url

if TYPE_CHECKING:
    from imgref.ctx import Ctx

__all__ = [
    "MAX_PAGES",
    "ParsesOptions",
    "Provider",
    "ProvidesHeaders",
    "as_int",
    "collect",
    "headers_for",
    "missing_env",
    "parse_options",
]

MAX_PAGES: int = 4
"""单次 ``collect`` 最多翻几页，防止游标实现有 bug 时无限循环。"""


def as_int(value: object) -> int | None:
    """把图源给的"数字"转成 ``int``，转不了就返回 ``None``。

    真实接口的字段类型很随意：同一家的 ``width`` 可能是 ``804``（int）或 ``"804"``
    （str），堆糖就是后者。``bool`` 虽然是 ``int`` 的子类，但语义上不是数字，排除掉。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


@runtime_checkable
class ProvidesHeaders(Protocol):
    """可选能力：按 URL 给出下载时需要的附加请求头。"""

    def headers_for(self, url: str) -> Mapping[str, str]:
        """返回下载 ``url`` 时要带的头（referer / cookie / token），没有就返回空字典。"""
        ...


@runtime_checkable
class ParsesOptions(Protocol):
    """可选能力：把 argparse 结果转成图源自己的类型化参数对象。"""

    def parse_options(self, ns: Namespace) -> object:
        """构造该图源私有参数的不可变对象。"""
        ...


class Provider(Protocol):
    """图源适配器协议：满足形状即可，不需要继承。

    Attributes:
        name: CLI 上使用的图源名（``bing`` / ``ddg`` / …），全局唯一。
        label: ``--help`` 里显示的一行中文说明。
        args: 该图源的**私有**参数声明；框架把它们挂进独立的 argument group。
        requires: 需要的环境变量名，框架只用来在帮助里标注"未配置"，不代它读取。
    """

    name: str
    label: str
    args: Sequence[Arg]
    requires: Sequence[str]

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Any) -> Page:
        """查一页。

        参数 ``opts`` 刻意是 ``Any``：每个图源的私有参数类型都不同，
        框架通过 :class:`ParsesOptions` 造出对应类型后原样传回，
        因此在各自的实现里它是**完全静态**的类型，只有协议这一处是动态的。
        """
        ...


def headers_for(provider: Provider, url: str) -> Mapping[str, str]:
    """取下载 ``url`` 时需要附加的请求头（图源没实现就返回空字典）。"""
    if isinstance(provider, ProvidesHeaders):
        return provider.headers_for(url)
    return {}


def parse_options(provider: Provider, ns: Namespace) -> object:
    """把 argparse 结果交给图源自己解析（没实现就返回 ``None``）。"""
    if isinstance(provider, ParsesOptions):
        return provider.parse_options(ns)
    return None


def missing_env(provider: Provider) -> list[str]:
    """返回该图源需要但当前缺失的环境变量名。"""
    return [name for name in provider.requires if not os.environ.get(name)]


async def collect(provider: Provider, ctx: Ctx, query: str, *, limit: int, opts: Any) -> list[ImageResult]:
    """按不透明游标翻页，直到凑够 ``limit`` 或没有下一页。

    框架只做 ``cursor = page.next_cursor`` 这一件事，**从不解释游标内容**；
    同时顺带做一次调用内的 URL 归一化去重（免费的、下载之前的预筛）。

    Args:
        provider: 目标图源。
        ctx: 网络上下文。
        query: 查询串。
        limit: 期望结果数。
        opts: 图源私有参数对象。

    Returns:
        去重后的结果列表，长度不超过 ``limit``；图源确实没有结果时返回空列表
        （"一个结果都没有"的退出码由上层决定，不算图源故障）。

    Raises:
        ProviderError: 图源在首页就失败，或返回的结构无法解析。
    """
    out: list[ImageResult] = []
    seen: set[str] = set()
    cursor: Cursor | None = None
    for _ in range(MAX_PAGES):
        page: Page = await provider.search(ctx, query, limit=limit - len(out), cursor=cursor, opts=opts)
        for result in page.results:
            key = normalize_url(result.image_url)
            if not result.image_url or key in seen:
                continue
            seen.add(key)
            out.append(result)
            if len(out) >= limit:
                return out
        if page.next_cursor is None or not page.results:
            break
        cursor = page.next_cursor
    return out
