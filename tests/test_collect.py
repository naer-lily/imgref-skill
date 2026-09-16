"""分页驱动 :func:`imgref.providers.base.collect` 测试。

框架只做 ``cursor = page.next_cursor``，从不解释游标内容——这里正是验证这一点。
另外验证 :attr:`Page.notes` 会被汇总出来交给上层。
"""

from __future__ import annotations

from collections.abc import Sequence

from imgref.providers.base import MAX_PAGES, Collected, collect
from imgref.types import Cursor, ImageResult, Page
from tests.helpers import FakeProvider, make_result, unused_ctx


class NoteProvider(FakeProvider):
    """每页都带一条说明的假图源。"""

    def __init__(self, pages: Sequence[Sequence[ImageResult]], notes: Sequence[str]) -> None:
        """记录分页内容与要带的说明。"""
        super().__init__(pages)
        self.notes = list(notes)

    async def search(self, ctx: object, query: str, *, limit: int, cursor: Cursor | None, opts: object) -> Page:
        """在父类结果上挂 notes。"""
        page = await super().search(ctx, query, limit=limit, cursor=cursor, opts=opts)  # type: ignore[arg-type]
        return Page(page.results, page.next_cursor, tuple(self.notes))


async def test_collect_walks_pages_until_limit() -> None:
    provider = FakeProvider([[make_result(index=1)], [make_result(index=2)], [make_result(index=3)]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=2, opts=None)
    assert [result.image_url for result in collected.results] == [
        "https://img.example.com/fake/1.png",
        "https://img.example.com/fake/2.png",
    ]
    assert provider.calls == [("q", 2, None), ("q", 1, "1")]


async def test_collect_stops_when_no_next_cursor() -> None:
    provider = FakeProvider([[make_result(index=1)]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(collected.results) == 1
    assert len(provider.calls) == 1


async def test_collect_dedupes_same_image_across_pages() -> None:
    same = make_result(index=1)
    provider = FakeProvider([[same], [same, make_result(index=2)]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(collected.results) == 2


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
        collected = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(collected.results) == 1


async def test_collect_skips_results_without_url() -> None:
    provider = FakeProvider([[make_result(index=1, image_url=""), make_result(index=2)]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=10, opts=None)
    assert len(collected.results) == 1


async def test_collect_is_bounded_by_max_pages() -> None:
    provider = FakeProvider([[make_result(index=index)] for index in range(20)])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=1000, opts=None)
    assert len(collected.results) == MAX_PAGES
    assert len(provider.calls) == MAX_PAGES


async def test_collect_returns_empty_without_raising() -> None:
    """"一个结果都没有"不是图源故障——退出码 2 由上层决定，不该在这里抛错。"""
    provider = FakeProvider([[]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=5, opts=None)
    assert collected.results == ()
    assert collected.notes == ()


async def test_collect_returns_partial_when_limit_reached_mid_page() -> None:
    provider = FakeProvider([[make_result(index=index) for index in range(5)]])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=3, opts=None)
    assert len(collected.results) == 3
    assert provider.calls == [("q", 3, None)]


async def test_collect_aggregates_notes_without_duplicates() -> None:
    """图源在 Page.notes 里带出来的说明要汇总给上层，重复的只留一条。"""
    provider = NoteProvider([[make_result(index=1)], [make_result(index=2)]], ["标签校验跳过（网络）"])
    async with unused_ctx() as ctx:
        collected = await collect(provider, ctx, "q", limit=10, opts=None)
    assert collected.notes == ("标签校验跳过（网络）",)


def test_collected_defaults() -> None:
    empty = Collected()
    assert empty.results == () and empty.notes == ()
