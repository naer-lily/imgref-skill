"""DuckDuckGo 适配器测试（离线夹具）。"""

from __future__ import annotations

import argparse
from collections.abc import Callable

import httpx
import pytest

from imgref.errors import ProviderError
from imgref.providers.ddg import DuckDuckGo, Options, extract_vqd, parse_ddg_json
from tests.helpers import mock_ctx

PAGE_HTML = "<html><head><script>const vqd='3-1234567890123456789012345678901234567890';</script></head></html>"
IJS_JSON = {
    "results": [
        {"image": "https://ex.com/full.jpg", "thumbnail": "https://ex.com/thumb.jpg", "title": "A cat", "url": "https://www.example.com/page", "width": 800, "height": 600},
        {"image": "https://ex.com/no-thumb.jpg"},
        {"image": "", "thumbnail": "https://ex.com/empty.jpg"},
    ],
    "next": "https://duckduckgo.com/i.js?q=x&s=100",
}


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ("<html>var vqd='12345-abc';</html>", "12345-abc"),
        ('<html>vqd="67890"</html>', "67890"),
        ('<input type="hidden" name="vqd" value="4-abcdef-9876543210">', "4-abcdef-9876543210"),
        ("<html>no token</html>", None),
        ("", None),
    ],
)
def test_extract_vqd(page: str, expected: str | None) -> None:
    assert extract_vqd(page) == expected


def test_extract_vqd_prefers_script_variable() -> None:
    page = "<script>const vqd='3-aaaa';</script><input name=\"vqd\" value=\"4-bbbb\">"
    assert extract_vqd(page) == "3-aaaa"


def test_parse_ddg_json() -> None:
    results = parse_ddg_json(IJS_JSON, 10)
    assert len(results) == 1
    first = results[0]
    assert first.provider == "ddg"
    assert first.thumb_url == "https://ex.com/thumb.jpg"
    assert first.image_url == "https://ex.com/full.jpg"
    assert first.title == "A cat"
    assert first.page_url == "https://www.example.com/page"
    assert (first.width, first.height) == (800, 600)


def test_parse_ddg_json_respects_limit() -> None:
    payload = {"results": [{"image": f"https://x/{i}.jpg", "thumbnail": f"https://x/{i}-t.jpg"} for i in range(5)]}
    assert len(parse_ddg_json(payload, 2)) == 2


@pytest.mark.parametrize("payload", [None, [], {}, {"results": "nope"}])
def test_parse_ddg_json_rejects_bad_shape(payload: object) -> None:
    with pytest.raises(ProviderError):
        parse_ddg_json(payload, 5)


def test_ddg_options() -> None:
    options = DuckDuckGo().parse_options(argparse.Namespace(region="cn-zh", safe=True))
    assert options == Options(region="cn-zh", safe=True)
    assert DuckDuckGo().parse_options(argparse.Namespace()).region == "wt-wt"


def test_ddg_declares_private_args() -> None:
    assert [arg.flags for arg in DuckDuckGo().args] == ["--region", "--safe"]


async def test_ddg_search_two_step_flow() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/i.js":
            return httpx.Response(200, json=IJS_JSON, headers={"content-type": "application/json"})
        return httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})

    async with mock_ctx(handler, label="ddg") as ctx:
        page = await DuckDuckGo().search(ctx, "cat", limit=10, cursor=None, opts=Options())
    assert [request.url.path for request in seen] == ["/", "/i.js"]
    assert seen[1].url.params["vqd"] == "3-1234567890123456789012345678901234567890"
    assert seen[1].url.params["s"] == "0"
    assert len(page.results) == 1
    assert page.next_cursor == IJS_JSON["next"]


async def test_ddg_follows_next_url_cursor_without_refetching_vqd() -> None:
    """游标对框架不透明：对方给的 next URL 直接照跟，连 vqd 都不用再取一次。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []}, headers={"content-type": "application/json"})

    cursor = "https://duckduckgo.com/i.js?q=cat&s=100&vqd=3-abc"
    async with mock_ctx(handler, label="ddg") as ctx:
        page = await DuckDuckGo().search(ctx, "cat", limit=10, cursor=cursor, opts=Options())
    assert len(seen) == 1
    assert str(seen[0].url) == cursor
    assert page.next_cursor is None


async def test_ddg_search_without_next_has_no_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/i.js":
            return httpx.Response(200, json={"results": []}, headers={"content-type": "application/json"})
        return httpx.Response(200, text=PAGE_HTML, headers={"content-type": "text/html"})

    async with mock_ctx(handler, label="ddg") as ctx:
        page = await DuckDuckGo().search(ctx, "cat", limit=10, cursor="100", opts=Options())
    assert page.next_cursor is None


async def test_ddg_missing_vqd_is_a_parse_error() -> None:
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(200, text="<html>nope</html>", headers={"content-type": "text/html"})
    async with mock_ctx(handler, label="ddg") as ctx:
        with pytest.raises(ProviderError) as info:
            await DuckDuckGo().search(ctx, "cat", limit=5, cursor=None, opts=Options())
    assert info.value.kind == "parse"
