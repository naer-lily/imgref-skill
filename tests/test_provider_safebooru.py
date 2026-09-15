"""safebooru.org 适配器测试。"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from typing import Any

import httpx

from imgref.providers.safebooru import Options, Safebooru, parse_safebooru_json
from tests.helpers import mock_ctx

SAFEBOORU_JSON: list[Any] = [
    {
        "id": 100,
        "directory": "847",
        "image": "d6aeef08f2546f2dc4fcccff11f1d555d6182c22.jpg",
        "file_url": "https://safebooru.org/images/847/d6aeef08f2546f2dc4fcccff11f1d555d6182c22.jpg",
        "sample_url": "https://safebooru.org/samples/847/sample_d6aeef08f2546f2dc4fcccff11f1d555d6182c22.jpg",
        "preview_url": "https://safebooru.org/thumbnails/847/thumbnail_d6aeef08f2546f2dc4fcccff11f1d555d.jpg",
        "sample": True,
        "width": 2896,
        "height": 4096,
        "sample_width": 850,
        "sample_height": 1202,
        "rating": "general",
        "source": "https://x.com/noghtylia/status/2098837434624577785",
        "owner": "danbooru",
    },
    {
        "id": 101,
        "file_url": "https://safebooru.org/images/847/small.png",
        "sample_url": "",
        "preview_url": "https://safebooru.org/thumbnails/847/thumbnail_small.png",
        "sample": False,
        "width": 300,
        "height": 200,
    },
    {"id": 102, "sample": True, "sample_url": "https://safebooru.org/samples/x.jpg"},
    "junk",
]


def test_parse_safebooru_json() -> None:
    results = parse_safebooru_json(SAFEBOORU_JSON, 10)
    assert len(results) == 2
    first = results[0]
    assert first.provider == "safebooru"
    assert first.image_url.endswith("d6aeef08f2546f2dc4fcccff11f1d555d6182c22.jpg")
    assert (first.thumb_url or "").startswith("https://safebooru.org/samples/")
    assert first.page_url == "https://safebooru.org/index.php?page=post&s=view&id=100"
    assert (first.width, first.height) == (2896, 4096)
    assert first.source == "https://x.com/noghtylia/status/2098837434624577785"


def test_parse_safebooru_uses_original_when_no_sample() -> None:
    """``sample`` 为 false 的小图没有大样，退回原图。"""
    second = parse_safebooru_json(SAFEBOORU_JSON, 10)[1]
    assert second.thumb_url == "https://safebooru.org/images/847/small.png"
    assert second.source is None


def test_parse_safebooru_rejects_non_list() -> None:
    assert parse_safebooru_json({"error": "nope"}, 5) == []
    assert parse_safebooru_json(None, 5) == []


def test_safebooru_has_no_private_args() -> None:
    assert list(Safebooru().args) == []
    assert Safebooru().requires == ()
    assert Safebooru().parse_options(Namespace()) == Options()


async def test_safebooru_search_builds_gelbooru_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SAFEBOORU_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat_ears", limit=10, cursor=None, opts=Options())
    params = seen[0].url.params
    assert params["page"] == "dapi"
    assert params["s"] == "post"
    assert params["q"] == "index"
    assert params["json"] == "1"
    assert params["tags"] == "cat_ears"
    assert params["pid"] == "0", "pid 从 0 起"
    assert len(page.results) == 2
    assert page.next_cursor is None


async def test_safebooru_advances_pid_on_full_page() -> None:
    full = [{**SAFEBOORU_JSON[0], "id": index} for index in range(3)]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=full, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat", limit=3, cursor="2", opts=Options())
    assert seen[0].url.params["pid"] == "2"
    assert page.next_cursor == "3"


async def test_safebooru_marks_cursor_exhausted() -> None:
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
        200, json=[SAFEBOORU_JSON[0]], headers={"content-type": "application/json"}
    )
    async with mock_ctx(handler, label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat", limit=10, cursor=None, opts=Options())
    assert page.next_cursor is None
