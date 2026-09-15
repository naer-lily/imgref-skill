"""Openverse 适配器测试（离线夹具）。"""

from __future__ import annotations

import argparse
from collections.abc import Callable

import httpx
import pytest

from imgref.errors import ProviderError
from imgref.providers.openverse import Options, Openverse, parse_openverse_json
from tests.helpers import mock_ctx

OPENVERSE_JSON = {
    "page_count": 3,
    "results": [
        {
            "url": "https://cdn.openverse.org/a.jpg",
            "thumbnail": "https://api.openverse.org/v1/images/1/thumb/",
            "title": "A tree",
            "foreign_landing_url": "https://www.flickr.com/photos/1",
            "license": "by",
            "width": 1024,
            "height": 768,
        },
        {"thumbnail": "https://cdn.openverse.org/no-url-thumb.jpg"},
        {"url": "https://cdn.openverse.org/b.jpg"},
    ],
}


def test_parse_openverse_json() -> None:
    results = parse_openverse_json(OPENVERSE_JSON, 10)
    assert len(results) == 2
    first = results[0]
    assert first.provider == "openverse"
    assert first.image_url == "https://cdn.openverse.org/a.jpg"
    assert first.title == "A tree"
    assert first.license == "by"
    assert first.page_url == "https://www.flickr.com/photos/1"
    assert results[1].thumb_url == results[1].image_url


@pytest.mark.parametrize("payload", [None, [], {}])
def test_parse_openverse_rejects_bad_shape(payload: object) -> None:
    with pytest.raises(ProviderError):
        parse_openverse_json(payload, 5)


def test_openverse_options_reads_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVERSE_TOKEN", "env-token")
    ns = argparse.Namespace(license_type="commercial", token=None)
    assert Openverse().parse_options(ns) == Options(license_type="commercial", token="env-token")


def test_openverse_cli_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVERSE_TOKEN", "env-token")
    ns = argparse.Namespace(license_type="all", token="cli-token")
    assert Openverse().parse_options(ns).token == "cli-token"


def test_openverse_declares_private_args() -> None:
    assert [arg.flags for arg in Openverse().args] == ["--license-type", "--token"]
    assert Openverse().requires == ()


async def test_openverse_search_sends_license_and_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OPENVERSE_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="openverse") as ctx:
        page = await Openverse().search(ctx, "tree", limit=8, cursor=None, opts=Options(license_type="commercial", token="tok"))
    params = seen[0].url.params
    assert params["q"] == "tree"
    assert params["page_size"] == "8"
    assert params["page"] == "1"
    assert params["license_type"] == "commercial"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert page.next_cursor == "2"


async def test_openverse_search_omits_license_when_all() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [], "page_count": 1}, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="openverse") as ctx:
        page = await Openverse().search(ctx, "tree", limit=5, cursor=None, opts=Options())
    assert "license_type" not in seen[0].url.params
    assert "authorization" not in seen[0].headers
    assert page.next_cursor is None
