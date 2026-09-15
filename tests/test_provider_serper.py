"""Serper 适配器测试（离线夹具）。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

import httpx
import pytest

from imgref.errors import ProviderError
from imgref.providers.serper import Options, Serper, parse_serper_json
from tests.helpers import mock_ctx

SERPER_JSON = {
    "images": [
        {
            "imageUrl": "https://cdn.example.com/a.jpg",
            "thumbnailUrl": "https://cdn.example.com/a-t.jpg",
            "title": "A gun",
            "link": "https://www.example.com/page",
            "imageWidth": 1600,
            "imageHeight": 900,
        },
        {"thumbnailUrl": "https://cdn.example.com/no-full.jpg"},
    ]
}


def test_parse_serper_json() -> None:
    results = parse_serper_json(SERPER_JSON, 10)
    assert len(results) == 1
    first = results[0]
    assert first.provider == "serper"
    assert first.image_url == "https://cdn.example.com/a.jpg"
    assert first.page_url == "https://www.example.com/page"
    assert (first.width, first.height) == (1600, 900)


@pytest.mark.parametrize("payload", [None, [], {}])
def test_parse_serper_rejects_bad_shape(payload: object) -> None:
    with pytest.raises(ProviderError):
        parse_serper_json(payload, 5)


def test_serper_requires_env_key() -> None:
    assert Serper().requires == ("SERPER_API_KEY",)
    assert [arg.flags for arg in Serper().args] == ["--gl", "--api-key"]


def test_serper_options_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "env-key")
    assert Serper().parse_options(argparse.Namespace(gl="us", api_key=None)).api_key == "env-key"
    assert Serper().parse_options(argparse.Namespace(gl=None, api_key="cli")).api_key == "cli"


async def test_serper_search_posts_body_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SERPER_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="serper") as ctx:
        page = await Serper().search(ctx, "m1911", limit=10, cursor=None, opts=Options(gl="cn", api_key="secret"))
    assert seen[0].method == "POST"
    assert seen[0].headers["x-api-key"] == "secret"
    assert json.loads(seen[0].content) == {"q": "m1911", "num": 10, "page": 1, "gl": "cn"}
    assert len(page.results) == 1
    assert page.next_cursor is None


async def test_serper_search_marks_cursor_when_full_page() -> None:
    many = {"images": [{"imageUrl": f"https://x/{i}.jpg"} for i in range(3)]}
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(200, json=many, headers={"content-type": "application/json"})
    async with mock_ctx(handler, label="serper") as ctx:
        page = await Serper().search(ctx, "q", limit=3, cursor="2", opts=Options(api_key="k"))
    assert page.next_cursor == "3"


async def test_serper_without_key_is_auth_error() -> None:
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(200, json={}, headers={"content-type": "application/json"})
    async with mock_ctx(handler, label="serper") as ctx:
        with pytest.raises(ProviderError) as info:
            await Serper().search(ctx, "q", limit=3, cursor=None, opts=Options(api_key=""))
    assert info.value.kind == "auth"
