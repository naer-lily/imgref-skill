"""yande.re 适配器测试（夹具照 2026-09 实测的真实字段写）。"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from imgref.errors import ProviderError
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


def _handler(
    posts: list[Any],
    known: tuple[str, ...] = ("cat_ears", "white_hair", "cat", "landscape"),
    *,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """按路径分派：``/tag.json`` 走标签校验，其余当帖子查询。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/tag.json":
            name = request.url.params["name"]
            if name in known:
                return httpx.Response(200, json=[{"id": 1, "name": name, "count": 100, "type": 0, "ambiguous": False}])
            return httpx.Response(200, json=[{"id": 2, "name": f"{name}_ish", "count": 3, "type": 0, "ambiguous": False}])
        return httpx.Response(200, json=posts, headers={"content-type": "application/json"})

    return handler


def _posts_request(seen: list[httpx.Request]) -> httpx.Request:
    """从请求序列里挑出帖子查询（标签校验走另一个端点）。"""
    return next(request for request in seen if request.url.path == "/post.json")


async def test_yandere_search_filters_rating_by_default() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(YANDERE_JSON, seen=seen), label="yandere") as ctx:
        page = await Yandere().search(ctx, "cat_ears white_hair", limit=10, cursor=None, opts=Options())
    assert _posts_request(seen).url.params["tags"] == "cat_ears white_hair rating:s"
    assert _posts_request(seen).url.params["page"] == "1"
    assert len(page.results) == 2
    assert page.next_cursor is None
    assert page.notes == (), "标签都对就不该多嘴"


async def test_yandere_validates_tags_before_searching() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(YANDERE_JSON, seen=seen), label="yandere") as ctx:
        await Yandere().search(ctx, "cat_ears", limit=5, cursor=None, opts=Options())
    lookups = [request for request in seen if request.url.path == "/tag.json"]
    assert [request.url.params["name"] for request in lookups] == ["cat_ears"]


async def test_yandere_drops_missing_tag_and_says_so() -> None:
    """标签不存在时接口只回空数组，跟"没搜到"长得一样——所以要提前挑出来并说明。"""
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(YANDERE_JSON, seen=seen), label="yandere") as ctx:
        page = await Yandere().search(ctx, "landscape no_humans", limit=5, cursor=None, opts=Options())
    assert _posts_request(seen).url.params["tags"] == "landscape rating:s"
    assert len(page.notes) == 1
    assert "no_humans" in page.notes[0]
    assert "no_humans_ish" in page.notes[0], "要带上相近候选"
    assert "已改用：landscape" in page.notes[0]


async def test_yandere_all_tags_missing_is_a_query_error() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler([], known=(), seen=seen), label="yandere") as ctx:
        with pytest.raises(ProviderError) as info:
            await Yandere().search(ctx, "no_humans scenery", limit=5, cursor=None, opts=Options())
    assert info.value.kind == "query"
    assert "no_humans" in info.value.message
    assert not [request for request in seen if request.url.path == "/post.json"], "全都不存在就别浪费一次搜索"


async def test_yandere_skips_validation_when_lookup_fails() -> None:
    """校验本身失败（网络）不能拖垮搜索，也不能把标签误判成不存在。"""
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tag.json":
            return httpx.Response(503, text="down")
        posts.append(request)
        return httpx.Response(200, json=YANDERE_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="yandere") as ctx:
        page = await Yandere().search(ctx, "landscape", limit=5, cursor=None, opts=Options())
    assert posts[0].url.params["tags"] == "landscape rating:s"
    assert len(page.notes) == 1
    assert "标签校验跳过" in page.notes[0]


async def test_yandere_does_not_validate_metatags() -> None:
    """``rating:s`` / ``-cat`` / ``a*`` 是图源自己的语法，不该拿去查标签表。"""
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(YANDERE_JSON, seen=seen), label="yandere") as ctx:
        await Yandere().search(ctx, "landscape -cat rating:s", limit=5, cursor=None, opts=Options(rating="all"))
    lookups = [request.url.params["name"] for request in seen if request.url.path == "/tag.json"]
    assert lookups == ["landscape"]


async def test_yandere_does_not_validate_on_later_pages() -> None:
    """校验是查询的属性，不是页的属性——翻页不该重复查。"""
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(YANDERE_JSON, seen=seen), label="yandere") as ctx:
        await Yandere().search(ctx, "landscape", limit=5, cursor="2", opts=Options())
    assert not [request for request in seen if request.url.path == "/tag.json"]


async def test_yandere_rating_all_drops_the_filter() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler([], seen=seen), label="yandere") as ctx:
        await Yandere().search(ctx, "cat", limit=5, cursor=None, opts=Options(rating="all"))
    assert _posts_request(seen).url.params["tags"] == "cat"


async def test_yandere_advances_page_cursor_on_full_page() -> None:
    full = [{**YANDERE_JSON[0], "id": index} for index in range(4)]
    async with mock_ctx(_handler(full), label="yandere") as ctx:
        page = await Yandere().search(ctx, "cat", limit=4, cursor=None, opts=Options())
    assert page.next_cursor == "2"
