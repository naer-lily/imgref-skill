"""safebooru.org 适配器测试。"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from imgref.errors import ProviderError
from imgref.providers.safebooru import Options, Safebooru, parse_safebooru_json, parse_tag_xml
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


def _tag_xml(entries: tuple[tuple[str, int], ...]) -> str:
    """造 Gelbooru tag 接口的 XML 响应（这个端点只给 XML）。"""
    body = " ".join(f'<tag type="0" count="{count}" name="{name}" ambiguous="false" id="1"/>' for name, count in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><tags type="array">{body}</tags>'


def _handler(
    posts: list[Any],
    known: tuple[str, ...] = ("cat_ears", "landscape", "cat"),
    *,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """按 ``s=`` 参数分派：``s=tag`` 走标签校验，``s=post`` 才是帖子查询。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.params.get("s") == "tag":
            name = request.url.params.get("name")
            if name is not None:
                return httpx.Response(200, text=_tag_xml(((name, 9774),) if name in known else ()), headers={"content-type": "application/xml"})
            pattern = (request.url.params.get("name_pattern") or "").rstrip("%")
            return httpx.Response(200, text=_tag_xml(((f"{pattern}_ish", 3),)), headers={"content-type": "application/xml"})
        return httpx.Response(200, json=posts, headers={"content-type": "application/json"})

    return handler


def _posts_request(seen: list[httpx.Request]) -> httpx.Request:
    """从请求序列里挑出帖子查询。"""
    return next(request for request in seen if request.url.params.get("s") == "post")


async def test_safebooru_search_builds_gelbooru_query() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(SAFEBOORU_JSON, seen=seen), label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat_ears", limit=10, cursor=None, opts=Options())
    params = _posts_request(seen).url.params
    assert params["page"] == "dapi"
    assert params["s"] == "post"
    assert params["q"] == "index"
    assert params["json"] == "1"
    assert params["tags"] == "cat_ears"
    assert params["pid"] == "0", "pid 从 0 起"
    assert len(page.results) == 2
    assert page.next_cursor is None
    assert page.notes == ()


async def test_safebooru_validates_tags_before_searching() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(SAFEBOORU_JSON, seen=seen), label="safebooru") as ctx:
        await Safebooru().search(ctx, "landscape", limit=5, cursor=None, opts=Options())
    lookups = [request for request in seen if request.url.params.get("s") == "tag"]
    assert [request.url.params.get("name") for request in lookups] == ["landscape"]
    assert len(seen) == 2, "一次校验 + 一次搜索"


async def test_safebooru_drops_missing_tag_and_gives_near_misses() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(SAFEBOORU_JSON, seen=seen), label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "landscape no_humans", limit=5, cursor=None, opts=Options())
    assert _posts_request(seen).url.params["tags"] == "landscape"
    assert len(page.notes) == 1
    assert "no_humans" in page.notes[0]
    assert "no_humans_ish" in page.notes[0], "不存在时要再用 name_pattern 找相近的"
    assert "已改用：landscape" in page.notes[0]


async def test_safebooru_all_tags_missing_is_a_query_error() -> None:
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler([], known=(), seen=seen), label="safebooru") as ctx:
        with pytest.raises(ProviderError) as info:
            await Safebooru().search(ctx, "no_humans scenery", limit=5, cursor=None, opts=Options())
    assert info.value.kind == "query"
    assert not [request for request in seen if request.url.params.get("s") == "post"]


async def test_safebooru_skips_validation_when_lookup_fails() -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("s") == "tag":
            return httpx.Response(200, text="not xml at all", headers={"content-type": "text/html"})
        posts.append(request)
        return httpx.Response(200, json=SAFEBOORU_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "landscape", limit=5, cursor=None, opts=Options())
    assert posts[0].url.params["tags"] == "landscape", "校验拿不到结果就按原样搜"
    assert len(page.notes) == 1
    assert "标签校验跳过" in page.notes[0]


def test_parse_tag_xml() -> None:
    assert parse_tag_xml(_tag_xml((("landscape", 9774), ("scenery", 64812)))) == [("landscape", 9774), ("scenery", 64812)]


def test_parse_tag_xml_empty_means_tag_absent() -> None:
    """对方对不存在的标签就回空 ``<tags>``——这是"标签不存在"的正当信号。"""
    assert parse_tag_xml(_tag_xml(())) == []


@pytest.mark.parametrize("text", ["", "@@@", "not xml at all"])
def test_parse_tag_xml_rejects_non_xml(text: str) -> None:
    """不是 XML 说明"读不到答案"，绝不能当成"标签不存在"——否则对方一改接口就全判错。"""
    with pytest.raises(ProviderError) as info:
        parse_tag_xml(text)
    assert info.value.kind == "parse"


async def test_safebooru_advances_pid_on_full_page() -> None:
    full = [{**SAFEBOORU_JSON[0], "id": index} for index in range(3)]
    seen: list[httpx.Request] = []
    async with mock_ctx(_handler(full, seen=seen), label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat", limit=3, cursor="2", opts=Options())
    assert _posts_request(seen).url.params["pid"] == "2"
    assert page.next_cursor == "3"
    assert not [request for request in seen if request.url.params.get("s") == "tag"], "翻页不重复校验"


async def test_safebooru_marks_cursor_exhausted() -> None:
    async with mock_ctx(_handler([SAFEBOORU_JSON[0]]), label="safebooru") as ctx:
        page = await Safebooru().search(ctx, "cat", limit=10, cursor=None, opts=Options())
    assert page.next_cursor is None
