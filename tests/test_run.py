"""``search`` 编排测试：拼图、候选表、排除、截断、清理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from imgref.errors import NoResultsError
from imgref.grid import GridSpec
from imgref.manifest import read_cells
from imgref.providers.base import Provider
from imgref.run import SearchRequest, run_search
from tests.helpers import FakeProvider, image_handler, make_image, make_result, mock_ctx


def _thumbs(*seeds: int) -> dict[str, bytes]:
    return {f"https://thumb.example.com/fake/{index}.png": make_image(seed) for index, seed in enumerate(seeds, start=1)}


def _request(
    out_root: Path,
    provider: Provider,
    *,
    limit: int = 6,
    spec: GridSpec | None = None,
    exclude: tuple[Path, ...] = (),
    write_manifest: bool = True,
    keep: int = 10,
) -> SearchRequest:
    return SearchRequest(
        provider=provider,
        query="m1911",
        opts=None,
        limit=limit,
        spec=spec if spec is not None else GridSpec(cols=3, rows=2, cell=64),
        out_root=out_root,
        keep=keep,
        exclude=exclude,
        write_manifest=write_manifest,
    )


async def test_run_search_produces_grid_table_and_manifest(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    async with mock_ctx(image_handler(_thumbs(0x0F0F, 0x3333)), label="fake") as ctx:
        outcome = await run_search(ctx, _request(out_root, provider))
    assert outcome.grid_path.is_file()
    assert outcome.manifest_path is not None and outcome.manifest_path.is_file()
    assert [row.ordinal for row in outcome.rows] == [1, 2]
    assert outcome.rows[0].ref_id == "fake|https://img.example.com/fake/1.png"
    assert outcome.rows[0].size == "1200x900"
    assert outcome.rows[0].source == "page.example.com"
    assert outcome.dropped == 0
    assert outcome.candidates == 2
    assert not outcome.warnings
    thumbs = sorted(path.name for path in (outcome.run_dir / "thumbs").iterdir())
    assert thumbs == ["01.png", "02.png"]
    cells = read_cells(outcome.manifest_path)
    assert cells[0].thumb_file == "thumbs/01.png"
    assert cells[0].ahash is not None


async def test_run_search_writes_manifest_relative_to_run_dir(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    async with mock_ctx(image_handler(_thumbs(0x0F0F)), label="fake") as ctx:
        outcome = await run_search(ctx, _request(out_root, provider))
    assert outcome.run_dir.parent == out_root
    assert outcome.run_dir.name.endswith("-fake-m1911")
    assert outcome.grid_path.parent == outcome.run_dir


async def test_run_search_skips_failed_thumbnails(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1), make_result(index=2)]])
    async with mock_ctx(image_handler({"https://thumb.example.com/fake/1.png": make_image(0x0F0F)}), label="fake") as ctx:
        outcome = await run_search(ctx, _request(out_root, provider))
    assert len(outcome.rows) == 1
    assert len(outcome.warnings) == 1
    assert "缩略图下载失败" in outcome.warnings[0]


async def test_run_search_raises_when_all_thumbnails_fail(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    async with mock_ctx(image_handler({}), label="fake") as ctx:
        with pytest.raises(NoResultsError):
            await run_search(ctx, _request(out_root, provider))


async def test_run_search_raises_when_provider_is_empty(out_root: Path) -> None:
    provider = FakeProvider([[]])
    async with mock_ctx(image_handler({}), label="fake") as ctx:
        with pytest.raises(NoResultsError):
            await run_search(ctx, _request(out_root, provider))


async def test_run_search_excludes_by_hash_across_urls(out_root: Path) -> None:
    """换 URL 但同一张图（同 aHash）必须被排除——这正是按 ID 排除做不到的。"""
    shared = make_image(0x0F0F)
    first_provider = FakeProvider([[make_result(index=1)]])
    async with mock_ctx(image_handler({"https://thumb.example.com/fake/1.png": shared}), label="fake") as ctx:
        first = await run_search(ctx, _request(out_root, first_provider))
    assert first.manifest_path is not None

    second_provider = FakeProvider(
        [
            [
                make_result(index=1, image_url="https://other.example.com/copy.png", thumb_url="https://other.example.com/copy-t.png"),
                make_result(index=2),
            ]
        ]
    )
    handler = image_handler(
        {
            "https://other.example.com/copy-t.png": shared,
            "https://thumb.example.com/fake/2.png": make_image(0x3333),
        }
    )
    async with mock_ctx(handler, label="fake") as ctx:
        second = await run_search(ctx, _request(out_root, second_provider, exclude=(first.manifest_path,)))
    assert second.dropped == 1
    assert len(second.rows) == 1
    assert second.rows[0].ordinal == 1


async def test_run_search_raises_when_everything_excluded(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    handler = image_handler(_thumbs(0x0F0F))
    async with mock_ctx(handler, label="fake") as ctx:
        first = await run_search(ctx, _request(out_root, provider))
    assert first.manifest_path is not None
    async with mock_ctx(handler, label="fake") as ctx:
        with pytest.raises(NoResultsError) as info:
            await run_search(ctx, _request(out_root, provider, exclude=(first.manifest_path,)))
    assert "排除" in str(info.value)


async def test_run_search_warns_when_over_capacity(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=index) for index in range(1, 5)]])
    handler = image_handler({f"https://thumb.example.com/fake/{index}.png": make_image(seed) for index, seed in enumerate((0x0F0F, 0x3333, 0x1BE4, 0x5A5A), start=1)})
    async with mock_ctx(handler, label="fake") as ctx:
        outcome = await run_search(ctx, _request(out_root, provider, spec=GridSpec(cols=2, rows=1, cell=64)))
    assert len(outcome.rows) == 2
    assert any("超过拼图容量" in warning for warning in outcome.warnings)


async def test_run_search_prunes_old_runs(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    handler = image_handler(_thumbs(0x0F0F))
    for _ in range(3):
        async with mock_ctx(handler, label="fake") as ctx:
            await run_search(ctx, _request(out_root, provider, keep=2))
    runs = [path for path in out_root.iterdir() if path.is_dir()]
    assert len(runs) == 2


async def test_run_search_can_skip_manifest(out_root: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    async with mock_ctx(image_handler(_thumbs(0x0F0F)), label="fake") as ctx:
        outcome = await run_search(ctx, _request(out_root, provider, write_manifest=False))
    assert outcome.manifest_path is None
    assert not (outcome.run_dir / "results.json").exists()


async def test_run_search_reuses_cached_thumbnails(out_root: Path, cache_dir: Path) -> None:
    provider = FakeProvider([[make_result(index=1)]])
    handler = image_handler(_thumbs(0x0F0F))
    async with mock_ctx(handler, cache_dir=cache_dir, label="fake") as ctx:
        first = await run_search(ctx, _request(out_root, provider))
    async with mock_ctx(handler, cache_dir=cache_dir, label="fake") as ctx:
        second = await run_search(ctx, _request(out_root, provider))
    assert first.run_dir != second.run_dir
    assert (second.run_dir / "thumbs" / "01.png").read_bytes() == (first.run_dir / "thumbs" / "01.png").read_bytes()
