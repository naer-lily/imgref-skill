"""URL 归一化，只用于"明显是同一张图"的免费预筛。

判定"是不是同一张图"最终靠 aHash（见 :mod:`imgref.imaging`）；这里做的是
在**下载之前**就能做的廉价剔除：去掉 scheme 大小写、www 前缀、fragment、
追踪参数和常见尺寸参数。

注意 :func:`normalize_url` 只用于比较，**不要**拿它的返回值去发请求。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final
from urllib.parse import parse_qsl, urlsplit, urlunsplit

__all__ = ["domain_of", "normalize_url"]

_DROP_EXACT: Final[frozenset[str]] = frozenset(
    {"auto", "crop", "dpr", "fit", "fmt", "format", "h", "height", "ixid", "ixlib", "q", "quality", "resize", "scale", "sh", "size", "sw", "w", "width"}
)
_DROP_PREFIX: Final[tuple[str, ...]] = ("utm_", "ga_", "fbclid", "gclid", "msclkid")
_DROP_RE: Final = re.compile(r"^(?:w|h|width|height|q|quality|size|resize|fit|crop|auto|dpr|scale|sw|sh)$", re.IGNORECASE)


def _is_droppable(key: str) -> bool:
    lowered = key.lower()
    return lowered in _DROP_EXACT or any(lowered.startswith(p) for p in _DROP_PREFIX) or bool(_DROP_RE.match(key))


def normalize_url(url: str) -> str:
    """把 URL 归一化成比较用的键。

    会把 ``http`` 与 ``https`` 视作同一个键（同一张图换协议仍然是同一张图），
    因此返回的字符串**不能**用来发请求。

    Args:
        url: 任意 URL。

    Returns:
        归一化后的比较键；无法解析时原样返回去空白后的输入。
    """
    raw = url.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.netloc:
        return raw
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        pairs: Iterable[tuple[str, str]] = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_droppable(k))
    except ValueError:
        pairs = ()
    return urlunsplit(("https", host, parts.path, "&".join(f"{k}={v}" for k, v in pairs), ""))


def domain_of(url: str | None) -> str:
    """取 URL 的域名（去掉 ``www.``），解析失败返回空串。"""
    if not url:
        return ""
    try:
        host = urlsplit(url.strip()).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]
