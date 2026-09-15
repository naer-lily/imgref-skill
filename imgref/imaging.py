"""图像处理：aHash 去重指纹、尺寸探测、扩展名嗅探。

刻意不引入 ``imagehash`` 之类的依赖：aHash 本身就是十行代码，
自举环境越小越不容易在小内存服务器上出问题。
"""

from __future__ import annotations

import io
from typing import Final

from PIL import Image, UnidentifiedImageError

from imgref.errors import ImgrefError

__all__ = ["HASH_BITS", "ahash", "hamming", "image_size", "sniff_ext"]

HASH_BITS: Final = 64
"""aHash 的位数。"""

_FORMAT_EXT: Final[dict[str, str]] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "AVIF": ".avif",
}


def ahash(data: bytes) -> str:
    """计算 64 位平均哈希（aHash），返回 64 个 ``0``/``1`` 字符。

    这是**精确**匹配用的指纹：同图不同尺寸/不同图源通常仍得到同一串。

    警告:
        纯色图是退化输入——所有像素相等时每一位都是 ``0``，不同颜色的纯色图
        会互相误判为重复。写测试夹具时必须制造结构性差异，而不是整体亮度偏移
        （整体亮度偏移对 aHash 是不变量）。

    Args:
        data: 图片字节。

    Returns:
        长度为 :data:`HASH_BITS` 的 ``0``/``1`` 字符串。

    Raises:
        ImgrefError: 字节无法解码成图片。
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            gray = im.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            raw = gray.tobytes()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImgrefError(f"无法解码图片：{exc}") from exc
    if len(raw) != HASH_BITS:
        raise ImgrefError(f"aHash 尺寸异常：期望 {HASH_BITS} 字节，得到 {len(raw)}")
    average = sum(raw) / HASH_BITS
    return "".join("1" if byte > average else "0" for byte in raw)


def hamming(left: str, right: str) -> int:
    """两个等长指纹的汉明距离（不等长时抛 :class:`ImgrefError`）。"""
    if len(left) != len(right):
        raise ImgrefError(f"指纹长度不一致：{len(left)} vs {len(right)}")
    return sum(1 for a, b in zip(left, right, strict=True) if a != b)


def image_size(data: bytes) -> tuple[int, int] | None:
    """探测图片像素尺寸；无法解码时返回 ``None``。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def sniff_ext(data: bytes, fallback_url: str = "") -> str:
    """从字节内容嗅探扩展名，失败时退回 URL 后缀，再失败给 ``.img``。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format and im.format in _FORMAT_EXT:
                return _FORMAT_EXT[im.format]
    except (UnidentifiedImageError, OSError, ValueError):
        pass
    lowered = fallback_url.lower().split("?")[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"):
        if lowered.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".img"
