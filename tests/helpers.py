"""测试工具箱：确定性图片、假图源、MockTransport 版 Ctx。

**关于测试图片的重要提醒**：aHash 的退化输入是"整体亮度偏移"和"纯色"——
对它们做任何亮度变化都不改变指纹。所以这里的 :func:`make_image` 生成的是
**结构性**差异（4×4 黑白大色块，能挺过 8×8 降采样），不是亮度差异。
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

from imgref.cache import BlobCache
from imgref.ctx import Ctx
from imgref.types import Cursor, ImageResult, Page

__all__ = [
    "BLOCK_SEEDS",
    "FakeProvider",
    "image_handler",
    "json_handler",
    "make_image",
    "make_result",
    "mock_ctx",
    "unused_ctx",
]

BLOCK_SEEDS: tuple[int, ...] = (0x0F0F, 0x3333, 0x1BE4, 0x5A5A, 0x0FF0, 0x6996)
"""成对互不相同的 16 位图案，用来生成结构上有区别的测试图。"""


def make_image(seed: int = 0x0F0F, size: int = 64) -> bytes:
    """按 ``seed`` 的 16 个 bit 画 4×4 黑白大色块，返回 PNG 字节。"""
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    block = max(size // 4, 1)
    for index in range(16):
        if (seed >> index) & 1:
            col, row = index % 4, index // 4
            draw.rectangle((col * block, row * block, (col + 1) * block - 1, (row + 1) * block - 1), fill=(16, 16, 16))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_result(provider: str = "fake", index: int = 1, **overrides: Any) -> ImageResult:
    """造一条候选结果。"""
    fields: dict[str, Any] = {
        "provider": provider,
        "image_url": f"https://img.example.com/{provider}/{index}.png",
        "thumb_url": f"https://thumb.example.com/{provider}/{index}.png",
        "page_url": f"https://page.example.com/{provider}/{index}",
        "title": f"image {index}",
        "width": 1200,
        "height": 900,
    }
    fields.update(overrides)
    return ImageResult(**fields)


class FakeProvider:
    """按页返回预设结果的假图源；满足 Provider 协议（结构性）。"""

    name: str = "fake"
    label: str = "测试图源"
    args: Sequence[Any] = ()
    requires: Sequence[str] = ()

    def __init__(self, pages: Sequence[Sequence[ImageResult]]) -> None:
        """记录分页内容。"""
        self.pages = [list(page) for page in pages]
        self.calls: list[tuple[str, int, Cursor | None]] = []

    async def search(self, ctx: Ctx, query: str, *, limit: int, cursor: Cursor | None, opts: Any) -> Page:
        """返回对应页，并把 ``next_cursor`` 设成下一页下标。"""
        self.calls.append((query, limit, cursor))
        index = int(cursor) if cursor else 0
        page = self.pages[index] if index < len(self.pages) else []
        next_cursor = str(index + 1) if index + 1 < len(self.pages) else None
        return Page(page[:limit], next_cursor)


def image_handler(mapping: Mapping[str, bytes], *, content_type: str = "image/png") -> Callable[[httpx.Request], httpx.Response]:
    """构造一个按 URL 返回图片字节的 MockTransport 处理器。"""

    def handler(request: httpx.Request) -> httpx.Response:
        data = mapping.get(str(request.url))
        if data is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, content=data, headers={"content-type": content_type})

    return handler


def json_handler(mapping: Mapping[str, Any], *, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    """构造一个按 URL 返回 JSON 的 MockTransport 处理器。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = mapping.get(str(request.url))
        if payload is None:
            return httpx.Response(404, json={"error": "missing"})
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(status, json=payload)

    return handler


@asynccontextmanager
async def mock_ctx(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cache_dir: Path | None = None,
    label: str = "test",
    max_bytes: int = 12 * 1024 * 1024,
    attempts: int = 2,
    concurrency: int = 4,
) -> AsyncIterator[Ctx]:
    """给一个由 MockTransport 驱动的 :class:`Ctx`（不发真实网络请求）。"""
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        cache = BlobCache(cache_dir, enabled=True) if cache_dir is not None else None
        yield Ctx(
            client=client,
            cache=cache,
            label=label,
            max_bytes=max_bytes,
            attempts=attempts,
            concurrency=concurrency,
            backoff=0.0,
        )


@asynccontextmanager
async def unused_ctx() -> AsyncIterator[Ctx]:
    """给一个"一旦发请求就炸"的 :class:`Ctx`：用于只测编排、不该联网的测试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"本测试不该发网络请求：{request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        yield Ctx(client=client, cache=None, label="unused", backoff=0.0)
