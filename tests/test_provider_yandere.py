"""yande.re 适配器测试（夹具照 2026-09 实测的真实字段写）。"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from typing import Any

import httpx

from imgref.providers.yandere import Options, Yandere, parse_yandere_json
from tests.helpers import mock_ctx

YANDERE_JSON: list[Any] = [
    {
        "id": 1268832,
        "tags": "neko sendou_senri skirt_lift thighhighs",
        "file_url": "https://files.yande.re/image/abc/yande.re%201268832%20neko.jpg",
        "sample_url": "https://files.yande.re/sample/abc/yande.re%201268832%20sample%20neko.jpg",
        "preview_url": "https://assets.yande.re/data/preview/36/81/abc.jpg",
        "width": 2894,
        "height": 4093,
        "sample_width": 1061,
        "sample_height": 1500,
        "rating": "s",
        "source": "https://www.pixiv.net/en/artworks/139046257",
        "md5": "36817f3d031dd67f91e97fcf5dc73b0a",
    },
    {"id": 2, "file_url": "https://files.yande.re/image/x/b.jpg", "preview_url": "https://assets.yande.re/x.jpg"},
    {"id": 3, "sample_url": "https://files.yande.re/sample/x/c.jpg"},
    {"id": 4},
    "junk",
]


def test_parse_yandere_json() -> None:
    results = parse_yandere_json(YANDERE_JSON, 10)
    assert len(results) == 2
    first = results[0]
    assert first.provider == "yandere"
    assert first.image_url.startswith("https://files.yande.re/image/")
    assert (first.thumb_url or "").startswith("https://files.yande.re/sample/"), "拼图要用大样，不是 150px 预览"
    assert first.page_url == "https://yande.re/post/show/1268832"
    assert (first.width, first.height) == (2894, 4093), "原图尺寸"
    assert first.source == "https://www.pixiv.net/en/artworks/139046257"


def test_parse_yandere_json_falls_back_to_original() -> None:
    """没有大样就用原图当缩略图。"""
    second = parse_yandere_json(YANDERE_JSON, 10)[1]
    assert second.thumb_url == "https://files.yande.re/image/x/b.jpg"


def test_parse_yandere_json_rejects_non_list() -> None:
    assert parse_yandere_json({"error": "nope"}, 5) == []
    assert parse_yandere_json(None, 5) == []


def test_parse_yandere_json_respects_limit() -> None:
    assert len(parse_yandere_json(YANDERE_JSON, 1)) == 1


def test_yandere_options() -> None:
    assert Yandere().parse_options(Namespace(rating="all")) == Options(rating="all")
    assert Yandere().parse_options(Namespace()).rating == "safe"


def test_yandere_declares_private_args() -> None:
    assert [arg.flags for arg in Yandere().args] == ["--rating"]
    assert Yandere().requires == ()


async def test_yandere_search_filters_rating_by_default() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=YANDERE_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="yandere") as ctx:
        page = await Yandere().search(ctx, "cat_ears white_hair", limit=10, cursor=None, opts=Options())
    assert seen[0].url.params["tags"] == "cat_ears white_hair rating:s"
    assert seen[0].url.params["page"] == "1"
    assert len(page.results) == 2
    assert page.next_cursor is None


async def test_yandere_rating_all_drops_the_filter() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[], headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="yandere") as ctx:
        await Yandere().search(ctx, "cat", limit=5, cursor=None, opts=Options(rating="all"))
    assert seen[0].url.params["tags"] == "cat"


async def test_yandere_advances_page_cursor_on_full_page() -> None:
    full = [{**YANDERE_JSON[0], "id": index} for index in range(4)]
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
        200, json=full, headers={"content-type": "application/json"}
    )
    async with mock_ctx(handler, label="yandere") as ctx:
        page = await Yandere().search(ctx, "cat", limit=4, cursor=None, opts=Options())
    assert page.next_cursor == "2"
