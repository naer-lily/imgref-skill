"""堆糖适配器测试。"""

from __future__ import annotations

from argparse import Namespace

import httpx
import pytest

from imgref.errors import ProviderError
from imgref.providers.duitang import Duitang, Options, next_start, parse_duitang_json
from tests.helpers import mock_ctx

DUITANG_JSON = {
    "status": 1,
    "data": {
        "total": 720,
        "next_start": 2,
        "object_list": [
            {
                "id": 1558614659,
                "msg": "猫",
                "photo": {
                    "id": "405536742",
                    "width": "804",
                    "height": "804",
                    "path": "https://a-ssl.dtstatic.com/uploads/blog/202508/02/aLSmjXeYs0V2DVM.jpg",
                    "size": "515315",
                    "file_type_code": "1",
                },
            },
            {"id": 2, "msg": "no photo"},
            {"id": 3, "photo": {"path": ""}},
            "junk",
        ],
    },
}


def test_parse_duitang_json_coerces_string_dimensions() -> None:
    results = parse_duitang_json(DUITANG_JSON, 10)
    assert len(results) == 1
    first = results[0]
    assert first.provider == "duitang"
    assert first.image_url == "https://a-ssl.dtstatic.com/uploads/blog/202508/02/aLSmjXeYs0V2DVM.jpg"
    assert first.thumb_url == first.image_url, "堆糖没有单独的缩略图"
    assert (first.width, first.height) == (804, 804), "接口给的是字符串，必须转成 int"
    assert first.title == "猫"
    assert first.page_url == "https://www.duitang.com/blog/?id=1558614659"


def test_parse_duitang_json_requires_status_one() -> None:
    with pytest.raises(ProviderError) as info:
        parse_duitang_json({"status": 0, "data": {}}, 5)
    assert info.value.kind == "parse"


@pytest.mark.parametrize("payload", [None, [], "nope", {"status": 1}, {"status": 1, "data": []}])
def test_parse_duitang_json_rejects_bad_shape(payload: object) -> None:
    with pytest.raises(ProviderError):
        parse_duitang_json(payload, 5)


def test_parse_duitang_json_tolerates_missing_object_list() -> None:
    assert parse_duitang_json({"status": 1, "data": {}}, 5) == []


@pytest.mark.parametrize(
    ("payload", "current", "expected"),
    [
        (DUITANG_JSON, 0, "2"),
        (DUITANG_JSON, 2, None),
        (DUITANG_JSON, 5, None),
        ({"status": 1, "data": {}}, 0, None),
        ({"status": 1, "data": {"next_start": "12"}}, 0, "12"),
        (None, 0, None),
        ("nope", 0, None),
    ],
)
def test_next_start(payload: object, current: int, expected: str | None) -> None:
    assert next_start(payload, current) == expected


def test_duitang_has_no_private_args() -> None:
    assert list(Duitang().args) == []
    assert Duitang().requires == ()
    assert Duitang().parse_options(Namespace()) == Options()


async def test_duitang_search_uses_offset_cursor() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DUITANG_JSON, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="duitang") as ctx:
        page = await Duitang().search(ctx, "猫", limit=10, cursor=None, opts=Options())
    assert seen[0].url.params["kw"] == "猫"
    assert seen[0].url.params["start"] == "0"
    assert len(page.results) == 1
    assert page.next_cursor == "2"


async def test_duitang_search_echoes_cursor() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": 1, "data": {"object_list": []}}, headers={"content-type": "application/json"})

    async with mock_ctx(handler, label="duitang") as ctx:
        page = await Duitang().search(ctx, "猫", limit=10, cursor="24", opts=Options())
    assert seen[0].url.params["start"] == "24"
    assert page.next_cursor is None
