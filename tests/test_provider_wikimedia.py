"""维基共享资源适配器测试（离线夹具）。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

import httpx
import pytest

from imgref.ctx import BROWSER_UA
from imgref.errors import ProviderError
from imgref.providers.wikimedia import Options, Wikimedia, parse_wikimedia_json
from tests.helpers import mock_ctx

WIKI_JSON = {
    "query": {
        "pages": {
            "1": {
                "title": "File:Nebula.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Nebula.jpg",
                        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Nebula.jpg/300px-Nebula.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Nebula.jpg",
                        "width": 2000,
                        "height": 1000,
                        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
                    }
                ],
            },
            "2": {"title": "File:No-thumb.jpg"},
            "3": {
                "title": "File:Second.jpg",
                "imageinfo": [{"url": "https://u.wikimedia.org/2.jpg", "thumburl": "https://u.wikimedia.org/2-t.jpg"}],
            },
        }
    },
    "continue": {"gsroffset": 20, "continue": "-||"},
}


def test_parse_wikimedia_json() -> None:
    results = parse_wikimedia_json(WIKI_JSON, 10)
    assert len(results) == 2
    first = results[0]
    assert first.provider == "wikimedia"
    assert first.image_url.endswith("Nebula.jpg")
    assert (first.thumb_url or "").endswith("300px-Nebula.jpg")
    assert first.license == "CC BY-SA 4.0"
    assert first.page_url == "https://commons.wikimedia.org/wiki/File:Nebula.jpg"
    assert (first.width, first.height) == (2000, 1000)
    assert results[1].license is None


def test_parse_wikimedia_strips_html_from_license() -> None:
    payload = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:X.jpg",
                    "imageinfo": [
                        {
                            "url": "https://u/x.jpg",
                            "thumburl": "https://u/x-t.jpg",
                            "extmetadata": {"LicenseShortName": {"value": "<b>CC0</b> &amp; more"}},
                        }
                    ],
                }
            }
        }
    }
    assert parse_wikimedia_json(payload, 5)[0].license == "CC0 & more"


@pytest.mark.parametrize("payload", [None, [], "nope"])
def test_parse_wikimedia_rejects_non_object(payload: object) -> None:
    with pytest.raises(ProviderError):
        parse_wikimedia_json(payload, 5)


@pytest.mark.parametrize("payload", [{}, {"query": {}}, {"query": {"pages": "nope"}}])
def test_parse_wikimedia_tolerates_empty_shapes(payload: object) -> None:
    assert parse_wikimedia_json(payload, 5) == []


def test_wikimedia_options() -> None:
    options = Wikimedia().parse_options(argparse.Namespace(mime="jpeg", thumb_width=512))
    assert options == Options(mime="jpeg", thumb_width=512)
    assert Wikimedia().parse_options(argparse.Namespace()).thumb_width == 400


def test_wikimedia_declares_private_args() -> None:
    assert [arg.flags for arg in Wikimedia().args] == ["--mime", "--thumb-width"]


async def test_wikimedia_search_builds_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=WIKI_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="wikimedia") as ctx:
        page = await Wikimedia().search(ctx, "nebula", limit=10, cursor=None, opts=Options(mime="jpeg"))
    params = seen[0].url.params
    assert params["gsrsearch"] == "filemime:image/jpeg nebula"
    assert params["gsrnamespace"] == "6"
    assert params["iiurlwidth"] == "400"
    assert seen[0].headers["user-agent"] != BROWSER_UA, "wikimedia 必须用框架的诚实 UA，伪装浏览器会被 403"
    assert len(page.results) == 2
    assert page.next_cursor is not None
    assert json.loads(page.next_cursor) == {"gsroffset": 20, "continue": "-||"}


async def test_wikimedia_cursor_is_echoed_back() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"query": {"pages": {}}}, headers={"content-type": "application/json"})

    cursor = json.dumps({"gsroffset": 20, "continue": "-||"})
    async with mock_ctx(handler, label="wikimedia") as ctx:
        page = await Wikimedia().search(ctx, "nebula", limit=10, cursor=cursor, opts=Options())
    assert seen[0].url.params["gsroffset"] == "20"
    assert seen[0].url.params["continue"] == "-||"
    assert page.next_cursor is None


async def test_wikimedia_api_error_surfaces() -> None:
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
        200, json={"error": {"info": "bad query"}}, headers={"content-type": "application/json"}
    )
    async with mock_ctx(handler, label="wikimedia") as ctx:
        with pytest.raises(ProviderError) as info:
            await Wikimedia().search(ctx, "x", limit=5, cursor=None, opts=Options())
    assert "bad query" in info.value.message
