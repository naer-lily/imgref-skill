"""manifest 与 ``--exclude`` 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imgref.errors import ImgrefError, UsageError
from imgref.grid import GridSpec
from imgref.manifest import Cell, load_exclude, pick_ids, read_cells, write_manifest
from imgref.types import ImageResult


def _cell(ordinal: int, *, provider: str = "bing", index: int = 1, ahash: str | None = None) -> Cell:
    return Cell(
        ordinal=ordinal,
        image=ImageResult(
            provider=provider,
            image_url=f"https://img.example.com/{index}.jpg",
            thumb_url=f"https://thumb.example.com/{index}.jpg",
            page_url=f"https://page.example.com/{index}",
            title=f"t{index}",
            width=100,
            height=50,
            license="CC0",
            source="https://www.pixiv.net/artworks/1",
        ),
        ahash=ahash,
        thumb_file=f"thumbs/{ordinal:02d}.jpg",
    )


def _write(path: Path, cells: list[Cell], spec: GridSpec | None = None) -> Path:
    write_manifest(
        path,
        provider="bing",
        query="m1911",
        label="front",
        spec=spec or GridSpec(),
        grid_path=path.parent / "grid.jpg",
        cells=cells,
        warnings=["demo warning"],
    )
    return path


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1, ahash="1010"), _cell(2, index=2)])
    cells = read_cells(path)
    assert [cell.ordinal for cell in cells] == [1, 2]
    assert cells[0].image.image_url == "https://img.example.com/1.jpg"
    assert cells[0].image.license == "CC0"
    assert cells[0].image.source == "https://www.pixiv.net/artworks/1"
    assert cells[0].ahash == "1010"
    assert cells[0].thumb_file == "thumbs/01.jpg"
    assert cells[0].ref_id == "bing|https://img.example.com/1.jpg"


def test_manifest_json_shape(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1, ahash="1010")])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["provider"] == "bing"
    assert payload["query"] == "m1911"
    assert payload["label"] == "front"
    assert payload["grid"]["cols"] == 4
    assert payload["warnings"] == ["demo warning"]
    assert payload["cells"][0]["id"] == "bing|" + payload["cells"][0]["image_url"]


def test_read_cells_sorts_by_ordinal(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(2, index=2), _cell(1, index=1)])
    assert [cell.ordinal for cell in read_cells(path)] == [1, 2]


def test_read_cells_rejects_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ImgrefError):
        read_cells(bad)
    no_cells = tmp_path / "no-cells.json"
    no_cells.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ImgrefError):
        read_cells(no_cells)
    not_object = tmp_path / "list.json"
    not_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ImgrefError):
        read_cells(not_object)


def test_load_exclude_collects_hashes_and_urls(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1, ahash="1010"), _cell(2, index=2)])
    exclude = load_exclude([path])
    assert exclude.hashes == {"1010"}
    assert exclude.blocks("1010", "https://other.example.com/x.jpg")
    assert exclude.blocks("9999", "HTTP://WWW.img.example.com/1.jpg?w=300")
    assert not exclude.blocks("9999", "https://other.example.com/x.jpg")


def test_load_exclude_matches_thumb_urls(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1, ahash=None)])
    exclude = load_exclude([path])
    assert exclude.hashes == frozenset()
    assert exclude.blocks("0000", "https://thumb.example.com/1.jpg")


def test_load_exclude_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        load_exclude([tmp_path / "nope.json"])


def test_load_exclude_merges_multiple(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.json", [_cell(1, ahash="1010")])
    second = _write(tmp_path / "b.json", [_cell(1, ahash="0101", index=9)])
    exclude = load_exclude([first, second])
    assert exclude.hashes == {"1010", "0101"}


def test_pick_ids(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1), _cell(2, index=2), _cell(3, index=3)])
    assert pick_ids([3, 1], path) == ["bing|https://img.example.com/3.jpg", "bing|https://img.example.com/1.jpg"]


def test_pick_ids_reports_available_ordinals(tmp_path: Path) -> None:
    path = _write(tmp_path / "results.json", [_cell(1)])
    with pytest.raises(UsageError) as info:
        pick_ids([7], path)
    assert "1" in str(info.value)
