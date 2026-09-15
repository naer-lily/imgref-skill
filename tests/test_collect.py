"""分页驱动 :func:`imgref.providers.base.collect` 测试。

框架只做 ``cursor = page.next_cursor``，从不解释游标内容——这里正是验证这一点。
"""

from __future__ import annotations

from imgref.providers.base import MAX_PAGES, collect
from tests.helpers import FakeProvider, make_result, unused_ctx


async def test_collect_walks_pages_until_limit() -> None:
    provider = FakeProvider([[make_result(index=1)], [make_result(index=2)], [make_result(index=3)]])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=2, opts=None)
    assert [result.image_url for result in results] == ["https://img.example.com/fake/1.png", "https://img.example.com/fake/2.png"]
    assert provider.calls == [("q", 2, None), ("q", 1, "1")]


async def test_collect_stops_when_no_next_cursor() -> None:
    provider = FakeProvider([[make_result(index=1)]])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(results) == 1
    assert len(provider.calls) == 1


async def test_collect_dedupes_same_image_across_pages() -> None:
    same = make_result(index=1)
    provider = FakeProvider([[same], [same, make_result(index=2)]])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(results) == 2


async def test_collect_dedupes_normalized_urls() -> None:
    provider = FakeProvider(
        [
            [
                make_result(index=1, image_url="https://img.example.com/a.jpg?w=300"),
                make_result(index=2, image_url="http://www.img.example.com/a.jpg"),
            ]
        ]
    )
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(results) == 1


async def test_collect_skips_results_without_url() -> None:
    provider = FakeProvider([[make_result(index=1, image_url=""), make_result(index=2)]])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(results) == 1


async def test_collect_is_bounded_by_max_pages() -> None:
    provider = FakeProvider([[make_result(index=index)] for index in range(20)])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=1000, opts=None)
    assert len(results) == MAX_PAGES
    assert len(provider.calls) == MAX_PAGES


async def test_collect_returns_empty_without_raising() -> None:
    """"一个结果都没有"不是图源故障——退出码 2 由上层决定，不该在这里抛错。"""
    provider = FakeProvider([[]])
    async with unused_ctx() as ctx:
        assert await collect(provider, ctx, "q", limit=5, opts=None) == []


async def test_collect_returns_partial_when_limit_reached_mid_page() -> None:
    provider = FakeProvider([[make_result(index=index) for index in range(5)]])
    async with unused_ctx() as ctx:
        results = await collect(provider, ctx, "q", limit=3, opts=None)
    assert len(results) == 3
    assert provider.calls == [("q", 3, None)]
