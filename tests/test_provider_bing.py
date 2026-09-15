"""必应适配器测试（离线夹具）。"""

from __future__ import annotations

import argparse
from collections.abc import Callable

import httpx
import pytest

from imgref.ctx import BROWSER_UA
from imgref.providers.bing import Bing, Options, parse_bing_html
from tests.helpers import mock_ctx

BING_HTML = (
    # 真实形态（2026-09 实测）：purl 是页面 URL，blob 里没有原图宽高
    '<a aria-label="Image result" role="link" class="iusc" style="height:180px;width:239px" '
    'm="{&quot;cid&quot;:&quot;7rIK3f+4&quot;,&quot;purl&quot;:&quot;https://www.x.com/p&quot;,'
    '&quot;murl&quot;:&quot;https://x.com/a.jpg&quot;,&quot;turl&quot;:&quot;https://x.com/a-t.jpg&quot;,'
    '&quot;md5&quot;:&quot;eeb2&quot;,&quot;t&quot;:&quot;Hello \\&quot;World\\&quot;&quot;,&quot;desc&quot;:&quot;pistol&quot;}"></a>'
    # 旧版/变体才有的 mw/mh：仍然容忍性地读
    '<a class="iusc" style="x" m="{&quot;murl&quot;:&quot;https://x.com/b.jpg&quot;,&quot;turl&quot;:&quot;https://x.com/b-t.jpg&quot;,&quot;mw&quot;:1024,&quot;mh&quot;:768}"></a>'
    '<a class="iusc" m="{not json}"></a>'
    '<a class="iusc" m="{&quot;turl&quot;:&quot;https://x.com/no-full.jpg&quot;}"></a>'
)


def test_parse_bing_html() -> None:
    results = parse_bing_html(BING_HTML, 10)
    assert len(results) == 2
    first = results[0]
    assert first.provider == "bing"
    assert first.image_url == "https://x.com/a.jpg"
    assert first.thumb_url == "https://x.com/a-t.jpg"
    assert first.title == 'Hello "World"'
    assert first.page_url == "https://www.x.com/p"
    assert (first.width, first.height) == (None, None), "真实 blob 没有 mw/mh，尺寸应为未知"


def test_parse_bing_html_tolerates_legacy_dimensions() -> None:
    """旧版 blob 里可能有 mw/mh；有就读，没有也不报错。"""
    second = parse_bing_html(BING_HTML, 10)[1]
    assert (second.width, second.height) == (1024, 768)
    assert second.page_url is None


def test_parse_bing_html_respects_limit() -> None:
    assert len(parse_bing_html(BING_HTML, 1)) == 1
    assert parse_bing_html("", 5) == []


def test_bing_options_from_namespace() -> None:
    options = Bing().parse_options(argparse.Namespace(safe="strict"))
    assert options == Options(safe="strict")
    assert Bing().parse_options(argparse.Namespace()).safe == "moderate"


def test_bing_declares_private_args() -> None:
    flags = [arg.flags for arg in Bing().args]
    assert flags == ["--safe"]
    assert Bing().requires == ()


def test_bing_headers_include_referer() -> None:
    assert Bing().headers_for("https://x.com/a.jpg")["Referer"] == "https://www.bing.com/"


async def test_bing_search_builds_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=BING_HTML, headers={"content-type": "text/html"})

    async with mock_ctx(handler, label="bing") as ctx:
        page = await Bing().search(ctx, "m1911", limit=10, cursor=None, opts=Options())
    assert len(page.results) == 2
    assert page.next_cursor is None
    assert seen[0].url.params["q"] == "m1911"
    assert seen[0].url.params["first"] == "1"
    assert "adlt" not in seen[0].url.params
    assert seen[0].headers["user-agent"] == BROWSER_UA


async def test_bing_search_passes_safe_filter_and_cursor() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="", headers={"content-type": "text/html"})

    async with mock_ctx(handler, label="bing") as ctx:
        await Bing().search(ctx, "q", limit=35, cursor="36", opts=Options(safe="strict"))
    assert seen[0].url.params["first"] == "36"
    assert seen[0].url.params["adlt"] == "strict"
    assert seen[0].url.params["count"] == "35"


async def test_bing_search_sets_cursor_when_page_is_full() -> None:
    full = "".join(
        f'<a class="iusc" m="{{&quot;murl&quot;:&quot;https://x.com/{i}.jpg&quot;,&quot;turl&quot;:&quot;https://x.com/{i}-t.jpg&quot;}}"></a>'
        for i in range(4)
    )
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(200, text=full, headers={"content-type": "text/html"})
    async with mock_ctx(handler, label="bing") as ctx:
        page = await Bing().search(ctx, "q", limit=4, cursor=None, opts=Options())
    assert len(page.results) == 4
    assert page.next_cursor == "5"


async def test_bing_surfaces_http_failure() -> None:
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(403, text="blocked")
    async with mock_ctx(handler, label="bing", attempts=1) as ctx:
        with pytest.raises(Exception) as info:
            await Bing().search(ctx, "q", limit=5, cursor=None, opts=Options())
    assert "403" in str(info.value)
