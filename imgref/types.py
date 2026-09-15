"""imgref 的纯数据类型。

这里只放数据，不放行为；图源协议在 :mod:`imgref.providers.base`。

关于 ID 的设计（重要）：ID 必须**自包含**，即 ``<provider>|<image_url>``。
拼图上的序号只是给视觉用的临时抓手，不是 ID——序号一旦离开那张图就没有意义，
而自包含 ID 可以脱离本轮上下文单独传递、单独解析。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeAlias

from imgref.errors import IdError

__all__ = ["ID_SEP", "Arg", "Cursor", "ImageResult", "Page", "parse_ref_id"]

ID_SEP: Final = "|"
"""自包含 ID 中 provider 与 URL 的分隔符。"""

# 不透明分页游标：谁家按 page、谁家按 offset、谁家给 continuation token，
# 全部由 provider 自己吞掉，框架只负责原样回传。
Cursor: TypeAlias = str


@dataclass(frozen=True, slots=True)
class ImageResult:
    """一张候选图。

    Attributes:
        provider: 产出这张图的图源名。
        image_url: 原图 URL（ID 的组成部分，也是 download 的目标）。
        page_url: 来源网页 URL，用于溯源。
        title: 图源给出的标题。
        thumb_url: 缩略图 URL；缺省时退化成 ``image_url``。
        width: 原图宽（图源声明值，未必准确）。
        height: 原图高（图源声明值，未必准确）。
        license: 版权标识（wikimedia / openverse 会给）。
    """

    provider: str
    image_url: str
    page_url: str | None = None
    title: str | None = None
    thumb_url: str | None = None
    width: int | None = None
    height: int | None = None
    license: str | None = None

    @property
    def ref_id(self) -> str:
        """自包含 ID：``<provider>|<image_url>``。"""
        return f"{self.provider}{ID_SEP}{self.image_url}"

    @property
    def thumb(self) -> str:
        """建立拼图时实际要下载的 URL。"""
        return self.thumb_url or self.image_url

    @property
    def size_label(self) -> str:
        """``1200x900`` 形式的尺寸标签，未知时为 ``?``。"""
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "?"


@dataclass(frozen=True, slots=True)
class Page:
    """一页搜索结果。

    Attributes:
        results: 本页结果。
        next_cursor: 下一页游标；``None`` 表示没有下一页。
    """

    results: Sequence[ImageResult]
    next_cursor: Cursor | None = None


@dataclass(frozen=True, slots=True)
class Arg:
    """provider 私有参数的声明式描述。

    图源适配器不需要认识 argparse：它只声明"我有哪些私有参数"，
    框架负责把它们挂进独立的 argument group，这样 ``--help`` 里
    "哪些参数所有图源共用、哪些是某个图源专属"一目了然。
    """

    flags: str | Sequence[str]
    help: str = ""
    choices: Sequence[str] | None = None
    default: Any = None
    metavar: str | None = None
    action: str | None = None
    type: Callable[[str], Any] | None = None


def parse_ref_id(raw: str) -> tuple[str, str]:
    """把自包含 ID 解析成 ``(provider, image_url)``。

    Args:
        raw: 形如 ``bing|https://example.com/a.jpg`` 的字符串。

    Returns:
        图源名与原图 URL。

    Raises:
        IdError: 格式非法，或其中的 URL 不是 http(s)。
    """
    provider, sep, url = raw.strip().partition(ID_SEP)
    if not sep or not provider or not url:
        raise IdError(f"ID 格式非法（应为 <provider>{ID_SEP}<image_url>）：{raw!r}")
    if not url.startswith(("http://", "https://")):
        raise IdError(f"ID 里的 URL 不是 http(s)：{url!r}")
    return provider, url
